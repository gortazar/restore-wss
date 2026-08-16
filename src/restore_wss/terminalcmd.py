"""Building the command line that reopens a terminal.

Every emulator spells "open a tab here, running this" differently, and some cannot do it at all,
so this is a small table rather than a clever abstraction — the same shape ``i3-resurrect`` and
``i3-restore`` settled on.

Two rules hold for every emulator:

* **argv, never a shell string.** The command came out of ``/proc`` as argv and it goes back as
  argv. Nothing is quoted into a string that something else will parse.
* **A tab whose command the policy refused still opens**, at its working directory, with a plain
  shell. Reopening the terminal in the right place is the part that always happens; re-running the
  command is the part that is asked about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Emulator:
    """How one terminal emulator is told to open tabs."""

    program: str
    #: The flag that starts a new tab in the same window, if it has one.
    tab_flag: str | None
    #: How a working directory is given.
    cwd_flag: str
    #: What separates the emulator's own options from the command to run.
    command_separator: str = "--"


EMULATORS = {
    "org.gnome.Terminal.desktop": Emulator("gnome-terminal", "--tab", "--working-directory"),
    "gnome-terminal-server": Emulator("gnome-terminal", "--tab", "--working-directory"),
    "org.gnome.Console.desktop": Emulator("kgx", "--tab", "--working-directory"),
    "Alacritty.desktop": Emulator("alacritty", None, "--working-directory"),
    "kitty.desktop": Emulator("kitty", None, "--directory"),
    "org.kde.konsole.desktop": Emulator("konsole", "--new-tab", "--workdir"),
}

DEFAULT_EMULATOR = EMULATORS["org.gnome.Terminal.desktop"]


def emulator_for(app_id: str) -> Emulator:
    return EMULATORS.get(app_id, DEFAULT_EMULATOR)


def terminal_argv(app_id: str, tabs) -> list[str]:
    """One command line that reopens every tab of a terminal window.

    gnome-terminal accepts several ``--tab`` groups in a single invocation, which is how a
    multi-tab window comes back as one window rather than as N windows. An emulator with no tab
    flag gets the first tab only, and the rest are dropped rather than opened as extra windows —
    silently doubling somebody's window count is worse than restoring less.
    """
    emulator = emulator_for(app_id)
    argv = [emulator.program]

    usable = list(tabs) or []
    if emulator.tab_flag is None:
        usable = usable[:1]

    if not usable:
        return argv

    for index, tab in enumerate(usable):
        if emulator.tab_flag is not None and index > 0:
            argv.append(emulator.tab_flag)
        elif emulator.tab_flag is not None:
            # gnome-terminal treats the first --tab as "open a tab", which is what a new window
            # starts with anyway; passing it keeps the grouping obvious in the argv.
            argv.append(emulator.tab_flag)
        if tab.cwd:
            argv.append(f"{emulator.cwd_flag}={tab.cwd}")
        if tab.run_command and tab.command:
            argv.append(emulator.command_separator)
            argv.extend(tab.command)
    return argv
