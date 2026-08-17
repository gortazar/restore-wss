"""Getting the tabs back, without duplicating what Firefox already restored.

The answered open question says the browser's own "restore previous session" stays **on**, and that
turns this from restoration into **reconciliation**. Firefox comes up with its own idea of the
session; the job here is only to fill in what it did not bring back, and to do nothing at all when
it brought back everything.

The rules, in the order they are applied:

1. A saved window whose URLs are all present in some live window is **already restored** — left
   alone, whatever else that window has since gained.
2. A saved window with nothing like it open is **requested**, with its tab order, pinned flags and
   active tab.
3. Tabs are never appended to an existing window: a window is created whole or not at all.
4. A saved window whose tabs were never captured (correlation refused, or no source) is **skipped**
   with a reason, because "open a browser window with unknown contents" is not a useful action.

Pure functions over saved and live window lists, so every one of those rules is a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .browser import BrowserWindow

#: Below this confidence a captured tab set is shown but not acted on: correlation was not sure
#: which browser window it belonged to, and reopening it could duplicate somebody's work.
ACT_ON_CONFIDENCE = 0.5


@dataclass
class BrowserAction:
    """One window to ask the browser for."""

    urls: list[str] = field(default_factory=list)
    pinned: list[bool] = field(default_factory=list)
    #: Index of the tab to activate, or ``-1``.
    active: int = -1
    #: Which compositor window (by snapshot id) this belongs to, for placement afterwards.
    window_id: str = ""
    title: str = ""

    def to_json(self) -> dict:
        return {
            "urls": self.urls,
            "pinned": self.pinned,
            "active": self.active,
            "window_id": self.window_id,
        }

    def describe(self) -> str:
        head = ", ".join(self.urls[:2])
        more = "" if len(self.urls) <= 2 else f" and {len(self.urls) - 2} more"
        return f"{len(self.urls)} tab(s): {head}{more}"


@dataclass
class BrowserPlan:
    actions: list[BrowserAction] = field(default_factory=list)
    #: ``(title, reason)`` for windows that will not be touched.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    #: Saved windows Firefox had already restored by itself.
    already_open: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.actions

    def describe(self) -> list[str]:
        lines = [f"tabs  open {action.describe()}" for action in self.actions]
        lines += [
            f"tabs  {title or 'a browser window'}: {reason}" for title, reason in self.skipped
        ]
        for title in self.already_open:
            lines.append(f"tabs  {title or 'a browser window'} was already restored by Firefox")
        return lines


def plan_browser_restore(
    saved: list[tuple[str, BrowserWindow]],
    live: list[BrowserWindow],
    *,
    act_on_confidence: float = ACT_ON_CONFIDENCE,
) -> BrowserPlan:
    """What to ask the browser for.

    ``saved`` is ``(compositor window id, block)`` pairs from the snapshot; ``live`` is what the
    browser reports right now (from the extension, or read from its session file).
    """
    plan = BrowserPlan()
    live_url_sets = [set(block.urls) for block in live]

    for window_id, block in saved:
        title = block.active_title
        if not block.tabs:
            plan.skipped.append((title, "its tabs were never captured"))
            continue
        if block.confidence < act_on_confidence:
            plan.skipped.append(
                (
                    title,
                    f"only {block.confidence:.0%} sure which browser window these tabs came from",
                )
            )
            continue

        urls = [tab.url for tab in block.tabs if tab.url and not tab.url.startswith("about:")]
        if not urls:
            plan.skipped.append((title, "no restorable URLs"))
            continue

        wanted = set(urls)
        satisfied_by = next(
            (index for index, open_urls in enumerate(live_url_sets) if wanted <= open_urls), None
        )
        if satisfied_by is not None:
            # Firefox already has these — possibly in a window that has since gained other tabs,
            # which is still "already restored" and still not ours to change.
            #
            # The live window is *consumed*: one open window can only account for one saved window.
            # Otherwise two saved windows showing the same page, of which Firefox restored one,
            # would both be called "already open" and the second would be lost.
            plan.already_open.append(title)
            live_url_sets.pop(satisfied_by)
            continue

        active = next(
            (index for index, tab in enumerate(block.tabs) if tab.active and tab.url in wanted), -1
        )
        plan.actions.append(
            BrowserAction(
                urls=urls,
                pinned=[tab.pinned for tab in block.tabs if tab.url in wanted],
                active=active,
                window_id=window_id,
                title=title,
            )
        )
    return plan
