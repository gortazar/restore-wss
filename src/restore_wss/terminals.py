"""What a terminal window was actually doing.

This is the feature the desktop session tools all skip and the tiling-WM ones get right
(``docs/similar-tools.md`` §4): a terminal window is not "a terminal", it is *a terminal in
``~/git/my-repo`` running ``claude -r``*, and all of that is legible from ``/proc`` with no
cooperation from the emulator.

The rules, each established against a real ``gnome-terminal`` in ``docs/platform-findings.md`` §3
and pinned by the fixtures in ``tests/fixtures/proc/``:

* **One tab is one session.** Each tab's shell is a direct child of the terminal server, is a
  session leader (``pid == session``) and has its own pty. Grouping the server's children by
  session id enumerates the tabs.
* **The tab's working directory is the shell's**, and it is exact. The server's own ``cwd`` is
  useless — it is wherever the server happened to be started.
* **The foreground job is the shallowest descendant whose process group is the tty's foreground
  group** (``tpgid`` on the session leader). That is the tty layer's own answer, not an inference
  from timing or CPU use; a shell with no descendant in that group is sitting at its prompt, and
  there is nothing to re-run. See :func:`foreground_job` for why "shallowest descendant" covers
  both interactive shells and ``bash -c``.

Everything here is a pure function of a :class:`~restore_wss.procwalk.Process` tree, so it is
tested against recorded trees rather than against a live desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .procwalk import Process
from .redaction import RedactionResult, redact

#: Window classes whose windows are terminal emulators. Deliberately a list rather than a guess:
#: treating the wrong application as a terminal means walking its process tree and recording its
#: command line, which is exactly the thing this project is careful about.
DEFAULT_TERMINAL_WM_CLASSES = (
    "gnome-terminal-server",
    "Gnome-terminal",
    "org.gnome.Terminal",
    "org.gnome.Console",
    "kgx",
    "Alacritty",
    "kitty",
    "foot",
    "Xfce4-terminal",
    "konsole",
)


@dataclass
class Tab:
    """One tab (or the single session of a single-window terminal)."""

    #: The shell's pid, for correlation while the session is alive. Not persisted as identity.
    pid: int = 0
    session: int = 0
    tty: int = 0
    cwd: str = ""
    #: The shell itself, e.g. ``["bash", "-i"]``.
    shell: list[str] = field(default_factory=list)
    #: The foreground job's argv, redacted. Empty when the shell is at its prompt.
    command: list[str] = field(default_factory=list)
    #: Which arguments were replaced, by index — so restore knows the command is incomplete and
    #: the user can see what was withheld rather than wondering what happened.
    redacted: list[int] = field(default_factory=list)

    @property
    def program(self) -> str:
        return self.command[0].rsplit("/", 1)[-1] if self.command else ""

    @property
    def has_redaction(self) -> bool:
        return bool(self.redacted)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"cwd": self.cwd, "tty": self.tty, "session": self.session}
        if self.shell:
            out["shell"] = self.shell
        if self.command:
            out["command"] = self.command
        if self.redacted:
            out["redacted"] = self.redacted
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Tab:
        return cls(
            pid=int(raw.get("pid", 0) or 0),
            session=int(raw.get("session", 0) or 0),
            tty=int(raw.get("tty", 0) or 0),
            cwd=str(raw.get("cwd", "")),
            shell=[str(a) for a in raw.get("shell", [])],
            command=[str(a) for a in raw.get("command", [])],
            redacted=[int(i) for i in raw.get("redacted", [])],
        )


def is_terminal(wm_class: str, terminal_classes=DEFAULT_TERMINAL_WM_CLASSES) -> bool:
    return wm_class in terminal_classes


def foreground_job(tab_leader: Process) -> Process | None:
    """The process the user is looking at in this tab, or ``None`` at a bare prompt.

    The foreground job is the **shallowest descendant whose process group is the tty's foreground
    group** (``tpgid`` on the session leader). Shallowest, because ``ssh -o ProxyCommand=…`` starts
    a helper in the same group and the job the user ran is the outer one.

    Two shapes both fall out of that single rule, which is why it is expressed this way rather
    than as "tpgid points elsewhere":

    * an **interactive** shell puts each job in its own group, so ``tpgid`` differs from the
      shell's own group and names the job (``bash -i`` → ``claude -r``);
    * a shell started as ``bash -c 'cmd'`` shares its group with what it runs, so ``tpgid`` equals
      the shell's group and the descendants in that group are the work.

    In both cases a shell with no descendant in the foreground group is a shell at its prompt.
    """
    target = tab_leader.tpgid
    if target < 0:
        return None
    for process in tab_leader.walk():
        if process is tab_leader:
            continue
        if process.pgrp == target:
            return process
    return None


def tabs_of(server: Process, *, redactor=redact) -> list[Tab]:
    """Every tab of a terminal server process."""
    found: list[Tab] = []
    for child in server.children:
        # A tab's shell is a session leader on its own pty. Anything else under the server (a
        # helper, a D-Bus worker) is not a tab.
        if child.session != child.pid or not child.tty_nr:
            continue
        job = foreground_job(child)
        command: list[str] = []
        redaction: RedactionResult | None = None
        if job is not None:
            redaction = redactor(job.cmdline)
            command = redaction.argv
        found.append(
            Tab(
                pid=child.pid,
                session=child.session,
                tty=child.tty_nr,
                # The shell's cwd, not the server's: the server's is wherever it was started.
                cwd=job.cwd if (job is not None and job.cwd) else child.cwd,
                shell=list(child.cmdline),
                command=command,
                redacted=list(redaction.redacted) if redaction else [],
            )
        )
    return found


def describe_terminal(server: Process, *, redactor=redact) -> dict[str, Any]:
    """The ``terminal`` block a window record carries in the snapshot."""
    return {
        "server_pid": server.pid,
        "tabs": [tab.to_json() for tab in tabs_of(server, redactor=redactor)],
    }
