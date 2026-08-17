"""Which compositor window is which browser window.

The crux of the browser feature, and the thing that decides whether it is useful: a
``Meta.Window`` with `wm_class` `firefox` has to be tied to one of the browser's own windows before
its tab set can be attached to the right workspace slot.

Signals, in the order the research found them useful
(``docs/browser-extensions-research.md`` §6):

1. **The active tab's title against the window title.** Firefox titles a window after its active
   tab (``"GNOME — Mozilla Firefox"``), so this is usually decisive, and it is scored with the same
   character-histogram distance v0.1 uses for windows in general.
2. **Geometry**, when the browser reported any. Weak on this machine — every window there is
   maximized at the same size — so it breaks ties rather than making decisions.
3. **Order**, as a last resort, and only when the counts match on both sides.

A window that cannot be matched confidently keeps its browser-ness and loses its tabs: the block
becomes "a browser window, tabs unknown" rather than someone else's tab set. That is the rule
``PLAN.md`` asks for, and the one this module exists to enforce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .browser import BrowserWindow
from .matcher import title_similarity
from .model import Window

#: Suffixes Firefox appends to the active tab's title. Stripped before comparing, because every
#: window shares them and they would flatten every score towards each other.
TITLE_SUFFIXES = (
    " — Mozilla Firefox",
    " - Mozilla Firefox",
    " — Firefox",
    " - Firefox",
    " — Mozilla Firefox Private Browsing",
)

#: Below this, no tab set is attached. Deliberately higher than the general window threshold: a
#: wrong tab set is more visibly wrong than a window in the wrong place.
MATCH_THRESHOLD = 0.75

#: The best candidate must beat the runner-up by this much, or the pairing is called ambiguous.
MIN_SPREAD = 0.08


@dataclass
class Correlation:
    window: Window
    block: BrowserWindow | None
    confidence: float
    reason: str


@dataclass
class CorrelationResult:
    matched: list[Correlation] = field(default_factory=list)
    #: Compositor windows that are browsers but got no tabs.
    unknown: list[Correlation] = field(default_factory=list)
    #: Browser windows with no compositor window — the browser reported a window the Shell does not
    #: show (a popup, or one that closed between the two reads).
    unclaimed: list[BrowserWindow] = field(default_factory=list)


def strip_suffix(title: str) -> str:
    for suffix in TITLE_SUFFIXES:
        if title.endswith(suffix):
            return title[: -len(suffix)]
    # A private window has no tab title in it at all; nothing to strip and nothing to match.
    return title


def _geometry_score(window: Window, geometry: dict | None) -> float:
    if not geometry or window.frame is None or not window.frame.is_known:
        return 0.0
    width, height = geometry.get("width"), geometry.get("height")
    if not width or not height:
        return 0.0
    # Firefox reports its *content* size in CSS pixels and the compositor reports the frame, so this
    # is a similarity, never an equality: within 10% counts as agreeing.
    dw = abs(window.frame.width - width) / max(window.frame.width, width)
    dh = abs(window.frame.height - height) / max(window.frame.height, height)
    return max(0.0, 1.0 - (dw + dh))


def score(window: Window, block: BrowserWindow, geometry: dict | None = None) -> tuple[float, str]:
    """How much ``block`` looks like the browser window behind ``window``, and why."""
    window_title = strip_suffix(window.title or "")
    tab_title = block.active_title or ""
    title = title_similarity(window_title, tab_title) if (window_title and tab_title) else 0.0

    geometry_agreement = _geometry_score(window, geometry)
    # Title dominates; geometry can lift a decent title match but never carry a bad one, because on
    # a machine where every window is maximized it agrees with everything.
    value = title * 0.85 + geometry_agreement * 0.15
    if title >= 0.999:
        return 1.0, "the window title is the active tab's title"
    reason = f"title {title:.2f}"
    if geometry_agreement:
        reason += f", geometry {geometry_agreement:.2f}"
    return value, reason


def correlate(
    windows: list[Window],
    blocks: list[BrowserWindow],
    geometries: list[dict] | None = None,
    *,
    threshold: float = MATCH_THRESHOLD,
    min_spread: float = MIN_SPREAD,
) -> CorrelationResult:
    """Pair browser windows with compositor windows, best score first."""
    geometries = geometries or []
    result = CorrelationResult()

    candidates: list[tuple[float, str, int, int]] = []
    for window_index, window in enumerate(windows):
        for block_index, block in enumerate(blocks):
            geometry = geometries[block_index] if block_index < len(geometries) else None
            value, reason = score(window, block, geometry)
            if value > 0.0:
                candidates.append((value, reason, window_index, block_index))
    candidates.sort(key=lambda item: (-item[0], item[2], item[3]))

    taken_windows: set[int] = set()
    taken_blocks: set[int] = set()

    for value, reason, window_index, block_index in candidates:
        if window_index in taken_windows or block_index in taken_blocks:
            continue
        if value < threshold:
            continue
        rival_value, rival_block = max(
            (
                (other_value, other_block)
                for other_value, _r, other_window, other_block in candidates
                if other_window == window_index
                and other_block != block_index
                and other_block not in taken_blocks
            ),
            default=(0.0, -1),
        )
        if rival_value and value - rival_value < min_spread:
            # Two browser windows look equally like this one. If they are showing the same tabs it
            # does not matter which is picked; if they are not, attaching either is a coin toss with
            # visible consequences, so refuse. An exact title match is no help here: two windows can
            # both be titled "Mozilla Firefox" and hold entirely different work.
            same_tabs = rival_block >= 0 and blocks[rival_block].urls == blocks[block_index].urls
            if not same_tabs:
                continue
        taken_windows.add(window_index)
        taken_blocks.add(block_index)
        block = blocks[block_index]
        block.confidence = round(value, 3)
        block.reason = f"{reason}; {block.reason}" if block.reason else reason
        result.matched.append(Correlation(windows[window_index], block, block.confidence, reason))

    # One browser window and one browser on screen: order is enough, and refusing there would be
    # pedantic rather than careful.
    if not result.matched and len(windows) == 1 and len(blocks) == 1:
        block = blocks[0]
        block.confidence = 0.6
        block.reason = "the only browser window on screen and the only one reported" + (
            f"; {block.reason}" if block.reason else ""
        )
        taken_windows.add(0)
        taken_blocks.add(0)
        result.matched.append(Correlation(windows[0], block, 0.6, "only candidate"))

    for index, window in enumerate(windows):
        if index not in taken_windows:
            result.unknown.append(
                Correlation(window, None, 0.0, "no browser window matched this one confidently")
            )
    result.unclaimed = [block for index, block in enumerate(blocks) if index not in taken_blocks]
    return result


#: Marker left on a browser window whose tabs could not be established, so the snapshot says
#: "a browser window, tabs unknown" instead of quietly having no browser block at all.
def unknown_block(family: str = "firefox", reason: str = "") -> BrowserWindow:
    return BrowserWindow(family=family, tabs=[], confidence=0.0, reason=reason or "tabs unknown")


_PRIVATE_TITLE = re.compile(r"private browsing", re.IGNORECASE)


def looks_private(window: Window) -> bool:
    """Whether a window is a private-browsing window, from its title alone.

    Belt and braces: the extension already refuses to report private windows, and the session file
    never contains them. This catches the third case — a private window on screen that neither
    source mentioned — so it is never handed a tab set by correlation.
    """
    return bool(_PRIVATE_TITLE.search(window.title or ""))
