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

VERSION = "0.2"


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
            _print_window_detail(window, out)

    plural = "" if len(snapshot.windows) == 1 else "s"
    print(
        f"{len(snapshot.windows)} window{plural} on {len(by_workspace)} workspace"
        f"{'' if len(by_workspace) == 1 else 's'}.",
        file=out,
    )


def _print_window_detail(window, out: TextIO) -> None:
    """The per-window extras: tabs for a browser, tabs-in-the-other-sense for a terminal.

    Printed under the window rather than as a separate section, because "which window were those
    seven tabs in" is the question the user is actually asking.
    """
    from .browser import browser_of

    block = browser_of(window)
    if block is not None:
        if not block.tabs:
            print(f"      tabs unknown ({block.reason})", file=out)
        else:
            count = len(block.tabs)
            preview = "; ".join((tab.title or tab.url)[:38] for tab in block.tabs[:3])
            more = "" if count <= 3 else f"; …{count - 3} more"
            confidence = "" if block.confidence >= 0.9 else f" ~{block.confidence:.0%} sure"
            print(f"      {count} tab(s){confidence}: {preview}{more}", file=out)

    terminal = window.extra.get("terminal") or {}
    for tab in terminal.get("tabs", []):
        command = " ".join(tab.get("command", [])) or "(shell)"
        print(f"      {tab.get('cwd', '?')}: {command}", file=out)


def print_plan(plan: dict, out: TextIO) -> None:
    """The review text: what restore is about to do, and what it will not do."""
    actions = plan.get("actions", [])
    if not actions:
        print("Nothing to restore: the snapshot matches what is already open.", file=out)
    else:
        needed = plan.get("workspace_count", 0)
        print(f"Restoring {len(actions)} window(s) across {needed} workspace(s):", file=out)
        for action in actions:
            confidence = action.get("confidence", 1.0)
            marker = " " if confidence >= 0.9 else "?"
            print(f" {marker} [{action['index']}] {action['description']}", file=out)

    for entry in plan.get("skipped", []):
        print(f"   skip  {entry['title'] or entry['wm_class']}: {entry['reason']}", file=out)
    for entry in plan.get("ambiguous", []):
        print(
            f"   ask   {entry['title']}: could be {entry['candidate']!r} "
            f"(score {entry['score']:.2f}) — too close to call, left alone",
            file=out,
        )
    for entry in plan.get("browser", []):
        print(f"   {entry['description']}", file=out)
    for entry in plan.get("vpn", []):
        print(f"   {entry['description']}", file=out)
    untouched = plan.get("untouched", [])
    if untouched:
        names = ", ".join(w["title"] or w["wm_class"] for w in untouched[:4])
        more = "" if len(untouched) <= 4 else f" and {len(untouched) - 4} more"
        print(f"   leaving alone: {names}{more}", file=out)


def _restore(args, client_factory=None, confirm=None) -> int:
    try:
        from .busclient import DaemonClient  # imported late: needs PyGObject

        client = (client_factory or DaemonClient)()
        plan = client.plan_restore()
    except Exception as error:  # noqa: BLE001
        print(f"restore-wss: cannot reach the daemon ({error}). Is restore-wss-daemon running?")
        return 1

    if getattr(args, "gui", False):
        from .review import run_review

        return run_review(client)

    if args.json:
        import json as _json

        print(_json.dumps(plan, indent=2))
        return 0

    print_plan(plan, sys.stdout)
    if not any(plan.get(key) for key in ("actions", "vpn", "browser")):
        return 0
    if args.dry_run:
        print("\n--dry-run: nothing was changed.")
        return 0

    if not args.yes:
        # Unattended restore is a flag, not the default: this launches a dozen applications.
        ask = confirm or (lambda: input("\nRestore these? [y/N] ").strip().lower())
        if ask() not in ("y", "yes"):
            print("Nothing was changed.")
            return 0

    result = client.restore()
    print()
    for entry in result.get("results", []):
        detail = f" — {entry['detail']}" if entry.get("detail") else ""
        print(f"{entry['state']:>7}  {entry['description']}{detail}")
    for entry in result.get("browser", []):
        print(f"{entry['state']:>7}  tabs {entry['detail']}")
    for entry in result.get("vpn", []):
        print(f"{entry['state']:>7}  vpn {entry['name']} — {entry['detail']}")
    failures = [r for r in result.get("results", []) if r["state"] != "done"]
    return 1 if failures else 0


def _list(args) -> int:
    """The snapshots on disk. There are at most two, by design (the answered open question rules
    out a history), and saying so is more useful than pretending there is a list to choose from."""
    import json as _json

    store = SnapshotStore(default_state_dir())
    found = []
    for label, path in (("current", store.current_path), ("previous", store.previous_path)):
        snapshot = SnapshotStore._load_one(path)  # noqa: SLF001 — reading one file, deliberately
        if snapshot is None:
            continue
        found.append(
            {
                "generation": label,
                "path": str(path),
                "captured_at": snapshot.captured_at,
                "captured": _format_age(snapshot.captured_at),
                "windows": len(snapshot.windows),
                "boot_id": snapshot.boot_id,
            }
        )

    if args.json:
        print(_json.dumps(found, indent=2))
        return 0
    if not found:
        print("restore-wss: no snapshots yet.")
        return 0
    for entry in found:
        print(
            f"{entry['generation']:>8}  {entry['captured']}  "
            f"{entry['windows']} window(s)  {entry['path']}"
        )
    print("\nOnly these two are kept: the current snapshot and the one before it.")
    return 0


def _diff(args, source: SnapshotSource | None = None) -> int:
    """What would change if the snapshot on disk were restored right now."""
    import json as _json

    from .diff import diff_windows

    saved = SnapshotStore(default_state_dir()).load()
    if saved is None:
        print("restore-wss: no snapshot on disk to compare against.")
        return 0

    live = source if source is not None else resolve_snapshot()
    if live.origin != "daemon" or live.snapshot is None:
        print("restore-wss: the daemon is not running, so there is nothing to compare with.")
        return 1

    difference = diff_windows(saved.windows, live.snapshot.windows)
    if args.json:
        print(
            _json.dumps(
                {
                    "only_in_snapshot": [w.to_json() for w in difference.only_in_snapshot],
                    "only_running": [w.to_json() for w in difference.only_running],
                    "moved": [
                        {"saved": s.to_json(), "running": r.to_json()} for s, r in difference.moved
                    ],
                    "unchanged": difference.unchanged,
                },
                indent=2,
            )
        )
        return 0

    if difference.is_empty:
        print("The snapshot matches what is running.")
        return 0
    for line in difference.describe():
        print(line)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="restore-wss", description=__doc__)
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="what is currently captured")
    status.add_argument("--json", action="store_true", help="print the snapshot as JSON")

    sub.add_parser("save", help="capture and write a snapshot now")

    listing = sub.add_parser("list", help="the snapshots on disk")
    listing.add_argument("--json", action="store_true", help="print them as JSON")

    difference = sub.add_parser("diff", help="the snapshot versus what is running now")
    difference.add_argument("--json", action="store_true", help="print it as JSON")

    restore = sub.add_parser("restore", help="put the workspaces back")
    restore.add_argument("--dry-run", action="store_true", help="print the plan and stop")
    restore.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    restore.add_argument("--json", action="store_true", help="print the plan as JSON and stop")
    restore.add_argument(
        "--gui", action="store_true", help="review it in a window instead of in the terminal"
    )

    sub.add_parser(
        "login-check",
        help="decide whether to offer a restore (run by the autostart entry, not by hand)",
    )
    sub.add_parser("daemon", help="run the capture loop (normally a systemd user unit)")
    return parser


def main(
    argv: list[str] | None = None,
    source: SnapshotSource | None = None,
    client_factory=None,
    confirm=None,
) -> int:
    """Entry point.

    ``source``, ``client_factory`` and ``confirm`` are injectable so that every line this prints
    can be tested without a bus, a compositor or a terminal to type into.
    """
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

    if args.command == "save":
        try:
            from .busclient import DaemonClient  # imported late: needs PyGObject

            print((client_factory or DaemonClient)().save())
            return 0
        except Exception as error:  # noqa: BLE001
            print(f"restore-wss: cannot reach the daemon ({error}). Is restore-wss-daemon running?")
            return 1

    if args.command == "list":
        return _list(args)

    if args.command == "diff":
        return _diff(args, source=source)

    if args.command == "restore":
        return _restore(args, client_factory=client_factory, confirm=confirm)

    if args.command == "login-check":
        from .config import load_config
        from .login import decide

        store = SnapshotStore(default_state_dir())
        decision = decide(store.load(), load_config())
        if not decision.offer:
            print(f"restore-wss: not offering a restore — {decision.reason}.")
            return 0
        print(f"restore-wss: {decision.reason}.")
        return _restore(
            argparse.Namespace(
                json=False,
                dry_run=False,
                # Unattended means unattended: no window, no question.
                yes=decision.unattended,
                gui=not decision.unattended,
            ),
            client_factory=client_factory,
        )

    if args.command == "daemon":
        from .daemon import run  # imported late: needs PyGObject

        return run()

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
