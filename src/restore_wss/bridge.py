"""The daemon's side of the file drop the browser host writes to.

The transport is a directory, not a bus, and the reason is in
``docs/browser-extensions-research.md`` §1: a native-messaging host executed by snap Firefox
inherits the browser's AppArmor profile, whose session-bus rules are a per-name allow-list that will
never contain ``org.gnome.RestoreWss``. So the host writes ``report.json`` and reads
``request.json``, and this module is the other end of both.

Everything here treats the drop as **untrusted input written by another program**: a missing file, a
half-written one, a report from a browser that has since exited, and a report old enough to be
meaningless are all normal states with defined answers, not errors.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .browser import FROM_EXTENSION, BrowserWindow, Tab

#: Where the drop directory appears from *outside* the sandbox. Inside it, Firefox's `$HOME` is
#: `~/snap/firefox/common`, so the host's `~/.mozilla/restore-wss` is the first of these.
DROP_DIRECTORIES = (
    "~/snap/firefox/common/.mozilla/restore-wss",
    "~/.mozilla/restore-wss",
    "~/.var/app/org.mozilla.firefox/.mozilla/restore-wss",
)

REPORT_NAME = "report.json"
REQUEST_NAME = "request.json"

#: A report older than this is not evidence about the session on screen now. Ten minutes is
#: generous: the extension reports on every change, debounced by 2.5 s, so a report this old means
#: the browser or the host has not been running.
MAX_REPORT_AGE_SECONDS = 600.0


@dataclass
class Report:
    windows: list[BrowserWindow]
    geometries: list[dict]
    age_seconds: float
    path: Path

    @property
    def is_fresh(self) -> bool:
        return self.age_seconds <= MAX_REPORT_AGE_SECONDS


def drop_directories(roots=DROP_DIRECTORIES) -> list[Path]:
    override = os.environ.get("RESTORE_WSS_BROWSER_DROP")
    if override:
        return [Path(override)]
    return [Path(os.path.expanduser(root)) for root in roots]


def read_report(roots=DROP_DIRECTORIES, now=None) -> Report | None:
    """What the extension last reported, or ``None`` if it has not.

    ``None`` is the normal state on a machine where the add-on is not installed, which is why the
    session-file reader exists; the daemon falls back to it rather than complaining.
    """
    now = time.time() if now is None else now
    for directory in drop_directories(roots):
        path = directory / REPORT_NAME
        try:
            raw = path.read_text()
            stat = path.stat()
        except OSError:
            continue
        try:
            document = json.loads(raw)
        except ValueError:
            # Being read mid-write is expected; the host writes atomically, so this means damaged.
            continue

        reported_at = float(document.get("reported_at") or stat.st_mtime)
        windows, geometries = _windows_from_report(document)
        if not windows:
            continue
        return Report(
            windows=windows,
            geometries=geometries,
            age_seconds=max(0.0, now - reported_at),
            path=path,
        )
    return None


def _windows_from_report(document: dict) -> tuple[list[BrowserWindow], list[dict]]:
    family = str(document.get("browser") or "firefox")
    version = str(document.get("version") or "")
    profile = str(document.get("profile") or "")

    windows: list[BrowserWindow] = []
    geometries: list[dict] = []
    for raw in document.get("windows") or []:
        tabs = [
            Tab(
                url=str(tab.get("url", "")),
                title=str(tab.get("title", "")),
                pinned=bool(tab.get("pinned", False)),
                active=bool(tab.get("active", False)),
                group=str(tab.get("group", "")),
            )
            for tab in raw.get("tabs") or []
            if str(tab.get("url", ""))
        ]
        if not tabs:
            continue
        windows.append(
            BrowserWindow(
                family=family,
                version=version,
                profile=profile,
                window_id=str(raw.get("id", "")),
                source=FROM_EXTENSION,
                tabs=tabs,
                reason="reported by the browser extension",
            )
        )
        geometries.append(
            {
                "x": raw.get("left"),
                "y": raw.get("top"),
                "width": raw.get("width"),
                "height": raw.get("height"),
                "maximized": raw.get("state") == "maximized",
            }
        )
    return windows, geometries


def write_request(windows: list[dict], roots=DROP_DIRECTORIES) -> Path | None:
    """Ask the extension to reopen these windows. Returns where it was left, or ``None``.

    Written atomically, and only into a directory that already exists: creating one would be
    pretending there is a browser to talk to.
    """
    for directory in drop_directories(roots):
        if not directory.is_dir():
            continue
        path = directory / REQUEST_NAME
        temporary = path.with_name(f".{REQUEST_NAME}.tmp")
        temporary.write_text(
            json.dumps({"requested_at": time.time(), "windows": windows}, indent=1)
        )
        os.replace(temporary, path)
        return path
    return None


def request_pending(roots=DROP_DIRECTORIES) -> bool:
    """Whether a request is still waiting to be picked up — the host deletes it when it acts."""
    return any((directory / REQUEST_NAME).exists() for directory in drop_directories(roots))
