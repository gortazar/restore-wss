"""Reading Firefox's own session store: windows and tabs with no extension installed.

This is the tier below the extension, and `docs/browser-extensions-research.md` §5 is why it
exists at all: the file turned out to carry every window, every tab with URL and title, pinned and
selected state, tab groups *and* per-window geometry. It needs no `tabs` permission, no add-on, no
signing and no install step, so it works on the first boot after installing.

What it cannot do is be *current*: Firefox writes it on its own cadence (the documented interval is
15 s, and the live file was 48 minutes old when probed, which is what an idle browser looks like).
Every block read from here is labelled ``source: "session-file"`` and carries the file's age, so the
review step can say "this is what Firefox last wrote down" instead of implying it is live.

Pure functions over a parsed document, plus one function that finds the profile — so the parsing
rules are testable against a committed fixture.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .browser import FROM_SESSION_FILE, BrowserWindow, Tab
from .mozlz4 import MozLz4Error, read_json

#: Where Firefox keeps its profiles, most specific first. The snap path is first because that is
#: what Ubuntu installs, and because a machine can have both.
PROFILE_ROOTS = (
    "~/snap/firefox/common/.mozilla/firefox",
    "~/.mozilla/firefox",
    "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
)

#: Session files inside a profile, newest-first in intent: ``recovery`` is the live one,
#: ``recovery.bak`` is the previous flush, and ``previous`` is the session before this one.
SESSION_FILES = (
    "sessionstore-backups/recovery.jsonlz4",
    "sessionstore-backups/recovery.baklz4",
    "sessionstore-backups/previous.jsonlz4",
)

#: URLs that are not worth restoring: a blank tab is not a tab the user opened.
IGNORED_URLS = ("about:blank", "about:newtab", "about:privatebrowsing", "")


@dataclass
class SessionSource:
    path: Path
    #: Profile directory name, e.g. ``cqdb58zj.default``. The name, never the full path — the path
    #: would put the home directory into a file that may be read elsewhere.
    profile: str
    #: Seconds since Firefox last wrote it. Reported, because staleness is the one real weakness.
    age_seconds: float


def find_session_files(roots=PROFILE_ROOTS, now=None) -> list[SessionSource]:
    """Every readable session file, freshest first."""
    now = time.time() if now is None else now
    found: list[SessionSource] = []
    for root in roots:
        base = Path(os.path.expanduser(root))
        if not base.is_dir():
            continue
        for profile in sorted(base.iterdir()):
            if not profile.is_dir():
                continue
            for name in SESSION_FILES:
                candidate = profile / name
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                found.append(
                    SessionSource(
                        path=candidate,
                        profile=profile.name,
                        age_seconds=max(0.0, now - stat.st_mtime),
                    )
                )
    found.sort(key=lambda source: source.age_seconds)
    return found


def _tab_from_json(raw: dict[str, Any], active_index: int, position: int) -> Tab | None:
    entries = raw.get("entries") or []
    if not entries:
        return None
    # The *last* entry is the current page; the earlier ones are that tab's back history, which is
    # deliberately out of scope (docs/limitations.md).
    index = raw.get("index")
    entry = (
        entries[index - 1] if isinstance(index, int) and 0 < index <= len(entries) else entries[-1]
    )
    url = str(entry.get("url") or entry.get("originalURI") or "")
    if url in IGNORED_URLS or url.startswith("about:reader"):
        return None
    return Tab(
        url=url,
        title=str(entry.get("title") or raw.get("label") or ""),
        pinned=bool(raw.get("pinned", False)),
        active=position == active_index,
        group=str(raw.get("groupId") or ""),
    )


def windows_from_document(document: dict[str, Any], profile: str = "", age: float = 0.0):
    """The browser windows a parsed session document describes.

    Returns ``(list[BrowserWindow], list[dict])`` — the blocks, and the raw per-window geometry
    (``x``, ``y``, ``width``, ``height``, ``maximized``) that correlation may use as a tiebreak.
    """
    blocks: list[BrowserWindow] = []
    geometries: list[dict[str, Any]] = []

    for position, raw in enumerate(document.get("windows") or []):
        selected = raw.get("selected")
        active_index = selected - 1 if isinstance(selected, int) else -1
        tabs = []
        for tab_position, raw_tab in enumerate(raw.get("tabs") or []):
            if raw_tab.get("hidden"):
                continue
            tab = _tab_from_json(raw_tab, active_index, tab_position)
            if tab is not None:
                tabs.append(tab)
        if not tabs:
            continue
        blocks.append(
            BrowserWindow(
                family="firefox",
                profile=profile,
                # The session file has no window id of its own, so the position in the file is the
                # only handle it offers — enough to tell two windows apart within one read.
                window_id=f"session-{position}",
                source=FROM_SESSION_FILE,
                tabs=tabs,
                reason=f"read from Firefox's session file, {int(age)}s old",
            )
        )
        geometries.append(
            {
                "x": raw.get("screenX"),
                "y": raw.get("screenY"),
                "width": raw.get("width"),
                "height": raw.get("height"),
                "maximized": raw.get("sizemode") == "maximized",
            }
        )
    return blocks, geometries


def read_windows(source: SessionSource | None = None, roots=PROFILE_ROOTS):
    """The browser windows Firefox last wrote down, freshest profile first.

    Returns ``(blocks, geometries, source)``; ``source`` is ``None`` when there is nothing readable,
    which is the normal state on a machine where Firefox has never run.
    """
    candidates = [source] if source is not None else find_session_files(roots)
    for candidate in candidates:
        try:
            document = read_json(candidate.path)
        except (MozLz4Error, OSError, ValueError):
            # Another program's private format: a file we cannot read contributes nothing.
            continue
        blocks, geometries = windows_from_document(
            document, profile=candidate.profile, age=candidate.age_seconds
        )
        if blocks:
            return blocks, geometries, candidate
    return [], [], None
