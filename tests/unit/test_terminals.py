"""The terminal rules, against process trees recorded from a real gnome-terminal.

The fixtures were produced by ``tools/proc-probe.py`` in a nested GNOME Shell 46; see
``docs/platform-findings.md`` §3. Testing against them rather than against a live desktop is what
makes these rules checkable in CI.
"""

import json
from pathlib import Path

from restore_wss.procwalk import Process
from restore_wss.terminals import describe_terminal, foreground_job, is_terminal, tabs_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "proc"


def load(name: str) -> Process:
    raw = json.loads((FIXTURES / name).read_text())
    return Process.from_json(raw["tree"][0])


def test_the_two_tabs_of_a_real_terminal_are_found():
    server = load("gnome-terminal-two-tabs.json")
    tabs = tabs_of(server)
    assert len(tabs) == 2
    # Each tab is its own session on its own pty — that is what makes them enumerable.
    assert len({tab.session for tab in tabs}) == 2
    assert len({tab.tty for tab in tabs}) == 2


def test_each_tab_reports_its_own_working_directory():
    server = load("gnome-terminal-two-tabs.json")
    directories = sorted(tab.cwd for tab in tabs_of(server))
    assert directories == ["/home/user", "/home/user/.cache/restore-wss-probe/my-repo"]
    # And not the server's, which is where it happened to be started.
    assert server.cwd == "/home/user"


def test_the_command_running_in_a_tab_is_captured():
    server = load("gnome-terminal-two-tabs.json")
    programs = sorted(tab.program for tab in tabs_of(server))
    assert programs == ["sleep", "ssh"]


def test_the_foreground_job_of_an_interactive_shell():
    """The scenario PLAN.md opens with: a shell in a repo running an agent."""
    shell = load("interactive-shell-foreground-job.json")
    job = foreground_job(shell)
    assert job is not None
    assert job.cmdline == ["claude", "-r"]
    assert job.cwd == "/home/user/.cache/restore-wss-probe/my-repo"


def test_the_program_name_comes_from_argv_not_from_exe():
    """`claude`'s exe is a versioned path; keying the allow-list on it would break every upgrade."""
    shell = load("interactive-shell-foreground-job.json")
    job = foreground_job(shell)
    assert "versions" in job.exe
    assert job.program == "claude"


def test_a_shell_at_its_prompt_has_no_command():
    shell = Process(pid=100, pgrp=100, session=100, tty_nr=34825, tpgid=100, cmdline=["bash", "-i"])
    assert foreground_job(shell) is None


def test_a_shell_at_its_prompt_still_yields_a_tab_with_its_directory():
    server = Process(pid=1, cmdline=["gnome-terminal-server"], cwd="/home/user")
    server.children.append(
        Process(
            pid=100,
            ppid=1,
            pgrp=100,
            session=100,
            tty_nr=34825,
            tpgid=100,
            cmdline=["bash", "-i"],
            cwd="/home/user/git/my-repo",
        )
    )
    tab = tabs_of(server)[0]
    assert tab.cwd == "/home/user/git/my-repo"
    assert tab.command == []


def test_helpers_under_the_server_are_not_mistaken_for_tabs():
    server = Process(pid=1, cmdline=["gnome-terminal-server"])
    server.children.append(
        # No controlling terminal, not a session leader: a worker, not a tab.
        Process(pid=50, ppid=1, pgrp=1, session=1, tty_nr=0, cmdline=["dconf-worker"])
    )
    assert tabs_of(server) == []


def test_secrets_are_redacted_at_capture_time():
    server = Process(pid=1, cmdline=["gnome-terminal-server"])
    shell = Process(
        pid=100,
        ppid=1,
        pgrp=100,
        session=100,
        tty_nr=1,
        tpgid=200,
        cmdline=["bash", "-i"],
        cwd="/home/user",
    )
    shell.children.append(
        Process(
            pid=200,
            ppid=100,
            pgrp=200,
            session=100,
            tty_nr=1,
            tpgid=200,
            cmdline=["mysql", "-u", "root", "--password", "hunter2"],
            cwd="/home/user",
        )
    )
    server.children.append(shell)
    tab = tabs_of(server)[0]
    assert "hunter2" not in " ".join(tab.command)
    assert tab.has_redaction


def test_the_snapshot_block_carries_every_tab():
    server = load("gnome-terminal-two-tabs.json")
    block = describe_terminal(server)
    assert block["server_pid"] == server.pid
    assert len(block["tabs"]) == 2
    assert all("cwd" in tab for tab in block["tabs"])


def test_only_declared_terminal_classes_are_treated_as_terminals():
    assert is_terminal("gnome-terminal-server")
    assert not is_terminal("org.gnome.TextEditor")
