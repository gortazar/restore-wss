"""Reading process trees out of ``/proc``.

The M0 probe established what is there and what it means
(``docs/platform-findings.md`` §3); this is that knowledge as library code, split in two on
purpose:

* :func:`read_tree` does the I/O — it is the only part that needs a real ``/proc``;
* everything in ``terminals.py`` is a pure function of the tree it returns, so the rules can be
  tested against the process-tree fixtures recorded from a real ``gnome-terminal``.

Nothing here raises on a process that disappears mid-walk. A ``/proc`` read is a race by nature:
between listing the children and reading one of them, the child may have exited, and the right
answer is "it is not in the tree" rather than an exception in the capture loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_PROC = Path(os.environ.get("RESTORE_WSS_PROC", "/proc"))

#: /proc/<pid>/stat truncates comm to 15 characters (TASK_COMM_LEN - 1), so
#: "gnome-terminal-server" is stored as "gnome-terminal-". Comparing against the untruncated name
#: silently matches nothing.
COMM_MAX = 15


@dataclass
class Process:
    """One process, with the fields that matter for working out what a terminal was doing."""

    pid: int
    comm: str = ""
    cmdline: list[str] = field(default_factory=list)
    cwd: str = ""
    exe: str = ""
    ppid: int = 0
    #: Process group. A shell puts each job it runs in its own group.
    pgrp: int = 0
    #: Session id. One terminal tab is one session, which is what makes tabs enumerable.
    session: int = 0
    #: Controlling terminal, 0 for none. Different per tab.
    tty_nr: int = 0
    #: The foreground process group of the controlling terminal, or -1. On the session leader this
    #: is the kernel's own answer to "which job is the user looking at".
    tpgid: int = -1
    children: list[Process] = field(default_factory=list)

    @property
    def program(self) -> str:
        """The name the command policy keys on.

        ``cmdline[0]``'s basename, never ``exe``: a version-managed program resolves ``exe`` to
        something like ``~/.local/share/claude/versions/2.1.233`` (observed), so keying on it would
        drop the program off the allow-list at every upgrade.
        """
        if self.cmdline:
            return os.path.basename(self.cmdline[0])
        return self.comm

    def walk(self):
        """This process and every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Process:
        """Rebuild a tree recorded by ``tools/proc-probe.py`` — how the fixtures are loaded."""
        return cls(
            pid=int(raw.get("pid", 0)),
            comm=str(raw.get("comm", "")),
            cmdline=[str(a) for a in raw.get("cmdline", [])],
            cwd=str(raw.get("cwd") or ""),
            exe=str(raw.get("exe") or ""),
            ppid=int(raw.get("ppid", 0) or 0),
            pgrp=int(raw.get("pgrp", 0) or 0),
            session=int(raw.get("session", 0) or 0),
            tty_nr=int(raw.get("tty_nr", 0) or 0),
            tpgid=int(raw.get("tpgid", -1)),
            children=[cls.from_json(child) for child in raw.get("children", [])],
        )


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _read_link(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _parse_stat(raw: str) -> dict[str, Any]:
    # comm can contain spaces and parentheses ("(sd-pam)"), so split around the *last* ')'.
    lhs, _, rhs = raw.rpartition(")")
    comm = lhs.partition("(")[2]
    fields = rhs.split()
    if len(fields) < 6:
        return {"comm": comm}
    return {
        "comm": comm,
        "ppid": int(fields[1]),
        "pgrp": int(fields[2]),
        "session": int(fields[3]),
        "tty_nr": int(fields[4]),
        "tpgid": int(fields[5]),
    }


def read_process(pid: int, proc: Path = DEFAULT_PROC) -> Process | None:
    base = proc / str(pid)
    stat = _read(base / "stat")
    if stat is None:
        return None
    raw_cmdline = _read(base / "cmdline") or ""
    return Process(
        pid=pid,
        cmdline=[arg for arg in raw_cmdline.split("\0") if arg],
        cwd=_read_link(base / "cwd"),
        exe=_read_link(base / "exe"),
        **_parse_stat(stat),
    )


def child_pids(pid: int, proc: Path = DEFAULT_PROC) -> list[int]:
    kids: list[int] = []
    task_dir = proc / str(pid) / "task"
    try:
        tasks = list(task_dir.iterdir())
    except OSError:
        return kids
    for task in tasks:
        raw = _read(task / "children")
        if raw:
            kids.extend(int(p) for p in raw.split())
    return sorted(set(kids))


def read_tree(pid: int, proc: Path = DEFAULT_PROC, max_depth: int = 8) -> Process | None:
    """A process and its descendants."""
    root = read_process(pid, proc)
    if root is None:
        return None
    if max_depth > 0:
        for child_pid in child_pids(pid, proc):
            child = read_tree(child_pid, proc, max_depth - 1)
            if child is not None:
                root.children.append(child)
    return root
