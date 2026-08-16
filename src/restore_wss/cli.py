"""``restore-wss`` — the command line.

Small and honest, per ``PLAN.md``: read-only commands print what is actually known and say where
they learned it, because "the daemon says there are seven windows" and "a file on disk from before
the reboot says there were seven windows" are different claims and the user is entitled to know
which one they are looking at.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import TextIO

from .model import Snapshot
from .storage import SnapshotStore, default_state_dir

VERSION = "0.1.0"


@dataclass
class SnapshotSource:
    """A snapshot plus the provenance of it.

    ``origin`` is one of ``daemon`` (live, authoritative), ``disk`` (the last snapshot written,
    which is what a restore would use) or ``missing``.
    """

    snapshot: Snapshot | None
    origin: str
    path: str | None = None


def resolve_snapshot() -> SnapshotSource:
    """Ask the daemon; fall back to the snapshot on disk; admit it when there is neither."""
    try:
        from .busclient import DaemonClient  # imported late: needs PyGObject

        snapshot = DaemonClient().get_snapshot()
        if snapshot is not None:
            return SnapshotSource(snapshot, "daemon")
    except Exception:  # noqa: BLE001 — any failure here means "no live daemon", which is normal
        pass

    store = SnapshotStore(default_state_dir())
    snapshot = store.load()
    if snapshot is None:
        return SnapshotSource(None, "missing")
    return SnapshotSource(snapshot, "disk", path=str(store.current_path))


def _format_age(captured_at: float) -> str:
    if not captured_at:
        return "unknown time"
    return datetime.fromtimestamp(captured_at).strftime("%Y-%m-%d %H:%M:%S")


def _print_status(source: SnapshotSource, out: TextIO) -> None:
    if source.snapshot is None:
        print(
            "restore-wss: nothing captured yet — no snapshot on disk and no daemon running.",
            file=out,
        )
        return

    snapshot = source.snapshot
    if source.origin == "daemon":
        print(
            f"Live session from the daemon, captured {_format_age(snapshot.captured_at)}.", file=out
        )
    elif source.origin == "disk":
        print(
            f"Snapshot on disk ({source.path}), captured {_format_age(snapshot.captured_at)} — "
            "the daemon is not running, so this may be out of date.",
            file=out,
        )

    if snapshot.is_empty:
        print("  no windows captured.", file=out)
        return

    by_workspace: dict[int, list] = {}
    for window in snapshot.windows:
        by_workspace.setdefault(window.workspace, []).append(window)

    for index in sorted(by_workspace):
        active = " (active)" if index == snapshot.active_workspace else ""
        # Workspaces are 0-based everywhere in the compositor and 1-based everywhere a human
        # looks at them, including GNOME's own switcher.
        print(f"Workspace {index + 1}{active}", file=out)
        for window in sorted(by_workspace[index], key=lambda w: w.stacking):
            name = window.title or window.wm_class or "(untitled)"
            where = window.monitor or "?"
            size = ""
            if window.frame is not None and window.frame.is_known:
                size = f" {window.frame.width}x{window.frame.height}"
            flags = "".join(
                flag
                for flag, on in (
                    ("M", window.maximized),
                    ("F", window.fullscreen),
                    ("m", window.minimized),
                )
                if on
            )
            print(f"  [{where}{size}]{' ' + flags if flags else ''} {name}", file=out)

    plural = "" if len(snapshot.windows) == 1 else "s"
    print(
        f"{len(snapshot.windows)} window{plural} on {len(by_workspace)} workspace"
        f"{'' if len(by_workspace) == 1 else 's'}.",
        file=out,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restore-wss", description=__doc__)
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="what is currently captured")
    status.add_argument("--json", action="store_true", help="print the snapshot as JSON")

    sub.add_parser("daemon", help="run the capture loop (normally a systemd user unit)")
    return parser


def main(argv: list[str] | None = None, source: SnapshotSource | None = None) -> int:
    """Entry point. ``source`` is injectable so the output can be tested without a bus."""
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.version:
        print(f"restore-wss {VERSION}")
        return 0

    if args.command == "status":
        resolved = source if source is not None else resolve_snapshot()
        if args.json:
            snapshot = resolved.snapshot or Snapshot()
            print(snapshot.dumps())
            return 0
        _print_status(resolved, sys.stdout)
        return 0

    if args.command == "daemon":
        from .daemon import run  # imported late: needs PyGObject

        return run()

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
