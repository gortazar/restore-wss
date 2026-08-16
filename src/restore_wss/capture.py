"""Turning what the compositor reports into a snapshot.

This is deliberately a pure function of two JSON documents — the extension's ``ListWindows`` and
``GetLayout`` replies — so the rules about *what is worth recording* can be tested against
fixtures, with no bus, no compositor and no clock. The daemon does the I/O; this decides the
content.

The rules, each of which exists because of something observed in ``docs/platform-findings.md``:

* A window that has not been identified yet is skipped. At ``window-created`` a window has no
  ``wm_class`` and ``Shell.WindowTracker`` answers with a synthetic ``window:N`` app id; recording
  that would fill the snapshot with windows that cannot be restored.
* A ``0x0`` frame is *unknown geometry*, not a window at the origin. It is dropped rather than
  written, so restore places the window by workspace and monitor and lets the app size itself.
* Monitors are keyed by connector name, because Mutter's monitor indices renumber on replug.
* Skipped windows are counted and reported, so "the snapshot has fewer windows than my desktop"
  is diagnosable rather than mysterious.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .model import Monitor, Rect, Snapshot, Window
from .terminals import describe_terminal, is_terminal

#: Window types worth restoring. Everything else — docks, dialogs owned by another window,
#: tooltips, the Shell's own actors — is either not restorable or not the user's session.
RESTORABLE_TYPES = {"normal"}


@dataclass
class CaptureResult:
    snapshot: Snapshot
    #: Why windows were left out, as ``reason -> count``. Reported by ``restore-wss status``.
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _frame_of(raw: dict[str, Any]) -> Rect | None:
    frame = raw.get("frame")
    if not isinstance(frame, dict):
        return None
    rect = Rect.from_json(frame)
    return rect if rect.is_known else None


def _monitor_connector(raw: dict[str, Any], connectors: dict[int, str]) -> str:
    connector = raw.get("monitor_connector")
    if isinstance(connector, str) and connector:
        return connector
    index = raw.get("monitor")
    if isinstance(index, int):
        return connectors.get(index, "")
    return ""


def capture(
    windows_json: str,
    layout_json: str,
    *,
    captured_at: float,
    boot_id: str = "",
) -> CaptureResult:
    """Build a snapshot from the extension's two replies."""
    windows_raw = json.loads(windows_json) if windows_json else []
    layout_raw = json.loads(layout_json) if layout_json else {}

    monitors = [Monitor.from_json(m) for m in layout_raw.get("monitors", []) or []]
    # The extension reports each window's monitor as an index, because that is what Meta gives it;
    # the connector name is the thing that survives a replug, so resolve it here.
    connectors = {
        int(m.get("index", -1)): str(m.get("connector", ""))
        for m in layout_raw.get("monitors", []) or []
        if isinstance(m, dict)
    }

    result = CaptureResult(
        snapshot=Snapshot(
            captured_at=captured_at,
            boot_id=boot_id,
            workspace_count=int(layout_raw.get("workspace_count", 0) or 0),
            active_workspace=int(layout_raw.get("active_workspace", 0) or 0),
            workspace_names=[str(n) for n in layout_raw.get("workspace_names", []) or []],
            monitors=monitors,
        )
    )

    for raw in windows_raw:
        if not isinstance(raw, dict):
            continue
        window_type = str(raw.get("window_type", "normal"))
        if window_type not in RESTORABLE_TYPES:
            result.skip(f"window type {window_type}")
            continue
        wm_class = str(raw.get("wm_class") or "")
        app_id = str(raw.get("app_id") or "")
        if not wm_class and not app_id:
            # Seen for real: at window-created there is no wm_class and no app yet.
            result.skip("not identified yet")
            continue
        if app_id.startswith("window:"):
            # Shell.WindowTracker's synthetic id for a window it has not matched to an app.
            result.skip("not identified yet")
            continue
        if raw.get("skip_taskbar"):
            result.skip("not in the taskbar")
            continue

        result.snapshot.windows.append(
            Window(
                id=str(raw.get("id", "")),
                wm_class=wm_class,
                title=str(raw.get("title") or ""),
                app_id=app_id,
                pid=int(raw.get("pid", 0) or 0),
                workspace=int(raw.get("workspace", 0) or 0),
                monitor=_monitor_connector(raw, connectors),
                frame=_frame_of(raw),
                maximized=bool(raw.get("maximized", False)),
                fullscreen=bool(raw.get("fullscreen", False)),
                minimized=bool(raw.get("minimized", False)),
                stacking=int(raw.get("stacking", 0) or 0),
            )
        )

    return result


def read_boot_id() -> str:
    """The current boot's id, or "" if the kernel will not say.

    Restore compares this with the snapshot's to tell a reboot from a log out and back in — the
    difference between "offer to restore everything" and "leave the user alone".
    """
    try:
        with open("/proc/sys/kernel/random/boot_id") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def apply_exclusions(result: CaptureResult, config) -> CaptureResult:
    """Drop what the user has asked never to record.

    Exclusion is applied here, on the way into the snapshot, rather than on the way out of it:
    an excluded application should never reach the disk in the first place.
    """
    if config is None:
        return result
    kept = []
    for window in result.snapshot.windows:
        if config.excludes_app(window.wm_class, window.app_id):
            result.skip("excluded by config")
            continue
        kept.append(window)
    result.snapshot.windows = kept
    return result


def enrich_terminals(result: CaptureResult, config=None, *, reader=None) -> CaptureResult:
    """Attach the ``terminal`` block to every terminal window.

    This is where the process tree is walked, and it is deliberately the *only* place: the tree of
    a window that is not a declared terminal is never read, because reading it means recording
    somebody's command line.

    ``reader`` is injectable so this can be tested against recorded trees.
    """
    if reader is None:
        from .procwalk import read_tree

        reader = read_tree

    terminals = config.terminals if config is not None else None
    for window in result.snapshot.windows:
        if not window.pid:
            continue
        if not (
            is_terminal(window.wm_class, terminals) if terminals else is_terminal(window.wm_class)
        ):
            continue
        tree = reader(window.pid)
        if tree is None:
            continue
        block = describe_terminal(tree)
        if config is not None and config.exclude_paths:
            # A tab whose working directory is under an excluded path is recorded as a bare
            # terminal: the user asked for that directory not to be written down.
            for tab in block["tabs"]:
                if config.excludes_path(tab.get("cwd", "")):
                    tab["cwd"] = ""
                    tab.pop("command", None)
        window.extra["terminal"] = block
    return result
