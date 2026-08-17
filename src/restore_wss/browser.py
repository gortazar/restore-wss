"""The browser half of a window record: which tabs were open in it.

A window belonging to a browser carries a ``browser`` block, and the shape is the same whichever
source produced it — that is the point of having the model separate from the two readers:

* the **extension** (tier 2, ``source: "extension"``) reports live state through the native host;
* the **session file** (``source: "session-file"``) is read from Firefox's own
  ``recovery.jsonlz4``, which needs no extension, no permission and no install step.

``docs/browser-extensions-research.md`` §5 is why the second one is a shipped tier rather than a
footnote: it turned out to carry every window, every tab with title and URL, pinned and selected
state, tab groups *and* per-window geometry.

Everything here is plain data with no behaviour beyond (de)serialisation, so the rules about what a
browser block *means* stay testable against fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Where a browser block came from. Recorded in the snapshot because "the browser told us" and "we
#: read its session file, which may be minutes stale" are different claims.
FROM_EXTENSION = "extension"
FROM_SESSION_FILE = "session-file"


@dataclass
class Tab:
    url: str = ""
    title: str = ""
    pinned: bool = False
    active: bool = False
    #: Tab group title, when the browser has groups and the tab is in one. Firefox 139+ and
    #: Chromium both have them; restore does not recreate groups (see docs/limitations.md).
    group: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url, "title": self.title}
        if self.pinned:
            out["pinned"] = True
        if self.active:
            out["active"] = True
        if self.group:
            out["group"] = self.group
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Tab:
        return cls(
            url=str(raw.get("url", "")),
            title=str(raw.get("title", "")),
            pinned=bool(raw.get("pinned", False)),
            active=bool(raw.get("active", False)),
            group=str(raw.get("group", "")),
        )


@dataclass
class BrowserWindow:
    """One browser window's worth of state, as attached to a compositor window record."""

    #: ``firefox`` today; the field exists so a snapshot says what it was rather than implying it.
    family: str = "firefox"
    version: str = ""
    #: Profile directory name (``cqdb58zj.default``), not the full path: it is an identity, and the
    #: path would leak the home directory into a file that may be read elsewhere.
    profile: str = ""
    #: The browser's own window id. Per-run only, like a compositor window id — it is what ties a
    #: report to a restore request within one session, never across a reboot.
    window_id: str = ""
    source: str = FROM_EXTENSION
    tabs: list[Tab] = field(default_factory=list)
    #: How sure the correlation between this browser window and the compositor window is, 0.0–1.0.
    #: Below the review threshold the tabs are shown but not acted on (B3).
    confidence: float = 1.0
    #: Why that confidence — shown in the review step rather than left as a number.
    reason: str = ""

    @property
    def urls(self) -> list[str]:
        return [tab.url for tab in self.tabs]

    @property
    def active_title(self) -> str:
        for tab in self.tabs:
            if tab.active:
                return tab.title
        return self.tabs[0].title if self.tabs else ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "family": self.family,
            "source": self.source,
            "tabs": [tab.to_json() for tab in self.tabs],
        }
        for key, value in (
            ("version", self.version),
            ("profile", self.profile),
            ("window_id", self.window_id),
            ("reason", self.reason),
        ):
            if value:
                out[key] = value
        if self.confidence < 1.0:
            out["confidence"] = self.confidence
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> BrowserWindow:
        return cls(
            family=str(raw.get("family", "firefox")),
            version=str(raw.get("version", "")),
            profile=str(raw.get("profile", "")),
            window_id=str(raw.get("window_id", "")),
            source=str(raw.get("source", FROM_EXTENSION)),
            tabs=[Tab.from_json(t) for t in raw.get("tabs", []) or []],
            confidence=float(raw.get("confidence", 1.0) or 1.0),
            reason=str(raw.get("reason", "")),
        )


#: Window classes that are a browser. Only these have a browser block attached, and only these are
#: looked for in a session file — the same "declare it, never guess" rule the terminal list follows.
DEFAULT_BROWSER_WM_CLASSES = ("firefox", "Firefox", "firefox-esr", "Navigator")


def is_browser(wm_class: str, browser_classes=DEFAULT_BROWSER_WM_CLASSES) -> bool:
    return wm_class in browser_classes


def browser_of(window) -> BrowserWindow | None:
    """The browser block on a window record, or ``None``."""
    raw = window.extra.get("browser")
    return BrowserWindow.from_json(raw) if isinstance(raw, dict) else None


def attach(window, block: BrowserWindow) -> None:
    window.extra["browser"] = block.to_json()
