#!/usr/bin/env python3
"""Dump what /proc says about a process and everything under it.

This is the M0 instrument for the question the desktop session tools all dodge: given the PID of a
terminal window, can the daemon work out that it is "a terminal in ~/git/my-repo running claude -r"?
It is deliberately standalone (no dependency on the package being built) and read-only.

    tools/proc-probe.py <pid> [<pid>...]        # one tree per pid, JSON on stdout
    tools/proc-probe.py --comm gnome-terminal-server

The output is the shape the fixtures in tests/fixtures/proc/ take, so a scenario recorded here can
be replayed in a unit test with no desktop attached.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROC = Path(os.environ.get("RESTORE_WSS_PROC", "/proc"))


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (OSError, PermissionError):
        return None


def _read_cmdline(pid: int) -> list[str]:
    raw = _read_text(PROC / str(pid) / "cmdline")
    if not raw:
        return []
    return [a for a in raw.split("\0") if a]


def _read_link(pid: int, name: str) -> str | None:
    try:
        return os.readlink(PROC / str(pid) / name)
    except (OSError, PermissionError):
        return None


def _read_stat(pid: int) -> dict[str, object]:
    raw = _read_text(PROC / str(pid) / "stat")
    if not raw:
        return {}
    # comm can contain spaces and parentheses, so split around the last ')'.
    lhs, _, rhs = raw.rpartition(")")
    comm = lhs.partition("(")[2]
    fields = rhs.split()
    if len(fields) < 20:
        return {"comm": comm}
    return {
        "comm": comm,
        "state": fields[0],
        "ppid": int(fields[1]),
        "pgrp": int(fields[2]),
        "session": int(fields[3]),
        "tty_nr": int(fields[4]),
        # The foreground process group of the controlling terminal: the single most useful
        # number in this whole file, because it says which of a shell's children the user is
        # actually looking at.
        "tpgid": int(fields[5]),
        "starttime": int(fields[19]),
    }


def _open_files(pid: int, limit: int = 40) -> list[dict[str, str]]:
    fd_dir = PROC / str(pid) / "fd"
    out: list[dict[str, str]] = []
    try:
        entries = sorted(fd_dir.iterdir(), key=lambda p: int(p.name))
    except (OSError, PermissionError):
        return out
    for entry in entries[:limit]:
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        out.append({"fd": entry.name, "target": target})
    return out


def children(pid: int) -> list[int]:
    """Direct children, from /proc/<pid>/task/*/children (cheap and authoritative)."""
    kids: list[int] = []
    task_dir = PROC / str(pid) / "task"
    try:
        tasks = list(task_dir.iterdir())
    except (OSError, PermissionError):
        return kids
    for task in tasks:
        raw = _read_text(task / "children")
        if raw:
            kids.extend(int(p) for p in raw.split())
    return sorted(set(kids))


def snapshot(pid: int, depth: int = 0, max_depth: int = 6) -> dict[str, object]:
    stat = _read_stat(pid)
    node: dict[str, object] = {
        "pid": pid,
        "cmdline": _read_cmdline(pid),
        "cwd": _read_link(pid, "cwd"),
        "exe": _read_link(pid, "exe"),
        "fds": _open_files(pid),
        **stat,
    }
    node["is_foreground"] = stat.get("tpgid", -1) == stat.get("pgrp", -2)
    if depth < max_depth:
        node["children"] = [snapshot(kid, depth + 1, max_depth) for kid in children(pid)]
    else:
        node["children"] = []
    return node


def pids_by_comm(comm: str) -> list[int]:
    """Processes whose comm matches.

    `comm` in /proc/<pid>/stat is truncated to 15 characters (TASK_COMM_LEN - 1), so
    `gnome-terminal-server` is stored as `gnome-terminal-` and an equality test finds nothing.
    Compare against the truncation, not the name the caller typed.
    """
    wanted = comm[:15]
    found = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        stat = _read_stat(int(entry.name))
        if stat.get("comm") == wanted:
            found.append(int(entry.name))
    return sorted(found)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pids", nargs="*", type=int)
    parser.add_argument("--comm", help="probe every process with this comm instead")
    args = parser.parse_args(argv)

    pids = list(args.pids)
    if args.comm:
        pids.extend(pids_by_comm(args.comm))
    if not pids:
        parser.error("give at least one pid, or --comm")

    json.dump([snapshot(pid) for pid in pids], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
