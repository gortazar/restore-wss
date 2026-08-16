"""Reopening a terminal: what command line is built, and what the policy lets through."""

import json

from restore_wss.model import Snapshot, Window
from restore_wss.plan import TERMINAL, TabPlan, build_plan
from restore_wss.policy import ALWAYS, NEVER, CommandPolicy
from restore_wss.restore import execute
from restore_wss.terminalcmd import terminal_argv


def terminal_window(tabs, **kwargs):
    window = Window(
        app_id="org.gnome.Terminal.desktop",
        wm_class="gnome-terminal-server",
        title="patxi@host: ~",
        workspace=1,
        **kwargs,
    )
    window.extra["terminal"] = {"server_pid": 1, "tabs": tabs}
    return window


def test_a_terminal_becomes_a_terminal_action_with_one_plan_per_tab():
    window = terminal_window(
        [
            {"cwd": "/home/user/git/my-repo", "command": ["claude", "-r"]},
            {"cwd": "/home/user", "command": ["ssh", "my-host"]},
        ]
    )
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy())
    action = plan.actions[0]
    assert action.kind == TERMINAL
    assert [tab.cwd for tab in action.tabs] == ["/home/user/git/my-repo", "/home/user"]
    assert all(tab.run_command for tab in action.tabs)


def test_a_command_that_is_not_whitelisted_is_not_re_run_but_the_tab_still_opens():
    window = terminal_window([{"cwd": "/home/user/deploy", "command": ["./deploy.sh", "prod"]}])
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy())
    tab = plan.actions[0].tabs[0]
    assert tab.cwd == "/home/user/deploy"
    assert not tab.run_command
    assert "not on the allow-list" in tab.reason
    assert "not re-running ./deploy.sh" in tab.describe()


def test_a_redacted_command_is_never_re_run_even_in_always_mode():
    window = terminal_window(
        [{"cwd": "/home/user", "command": ["mysql", "--password", "<redacted>"], "redacted": [2]}]
    )
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy(mode=ALWAYS))
    tab = plan.actions[0].tabs[0]
    assert not tab.run_command
    assert "redacted" in tab.reason


def test_never_mode_reopens_the_directories_and_nothing_else():
    window = terminal_window([{"cwd": "/home/user/git", "command": ["claude", "-r"]}])
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy(mode=NEVER))
    tab = plan.actions[0].tabs[0]
    assert tab.cwd == "/home/user/git"
    assert not tab.run_command


def test_the_plan_describes_a_terminal_in_words_a_person_can_check():
    window = terminal_window([{"cwd": "/home/user/git/my-repo", "command": ["claude", "-r"]}])
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy())
    assert "claude -r in /home/user/git/my-repo" in plan.actions[0].describe()


def test_the_command_line_reopens_every_tab_in_one_window():
    argv = terminal_argv(
        "org.gnome.Terminal.desktop",
        [
            TabPlan(cwd="/home/user/git", command=["claude", "-r"], run_command=True),
            TabPlan(cwd="/home/user", command=["ssh", "host"], run_command=True),
        ],
    )
    assert argv[0] == "gnome-terminal"
    assert argv.count("--tab") == 2
    assert "--working-directory=/home/user/git" in argv
    assert argv[-2:] == ["ssh", "host"]


def test_a_refused_command_leaves_a_plain_shell_in_the_right_directory():
    argv = terminal_argv(
        "org.gnome.Terminal.desktop",
        [TabPlan(cwd="/home/user/deploy", command=["./deploy.sh"], run_command=False)],
    )
    assert "--working-directory=/home/user/deploy" in argv
    assert "./deploy.sh" not in argv
    assert "--" not in argv


def test_an_emulator_without_tabs_gets_the_first_tab_only():
    argv = terminal_argv(
        "Alacritty.desktop",
        [TabPlan(cwd="/a", command=["htop"], run_command=True), TabPlan(cwd="/b")],
    )
    assert argv[0] == "alacritty"
    assert "/b" not in " ".join(argv)


def test_executing_a_terminal_action_spawns_argv_and_registers_the_placement():
    spawned = []
    expected = []

    class Core:
        def ensure_workspaces(self, count):
            return count

        def activate_workspace(self, index):
            pass

        def expect_window(self, desktop_id, placement_json):
            expected.append((desktop_id, json.loads(placement_json)))
            return "expect-1"

        def get_launch_report(self, launch_id):
            return json.dumps({"state": "placed", "strategy": "app-id-and-timing"})

    window = terminal_window([{"cwd": "/home/user/git", "command": ["claude", "-r"]}])
    plan = build_plan(Snapshot(windows=[window]), [], command_policy=CommandPolicy())
    result = execute(plan, Core(), wait_seconds=5, sleep=lambda _s: None, spawn=spawned.append)

    assert result.results[0].state == "done"
    assert spawned[0][0] == "gnome-terminal"
    assert "claude" in spawned[0]
    # The placement was registered with the compositor before the process was started.
    assert expected[0][0] == "org.gnome.Terminal.desktop"
    assert expected[0][1]["workspace"] == 1


def test_the_command_is_never_handed_to_a_shell():
    """Shell metacharacters in a captured command must arrive as literal argv, not be run."""
    argv = terminal_argv(
        "org.gnome.Terminal.desktop",
        [TabPlan(cwd="/tmp", command=["echo", "hi; rm -rf /"], run_command=True)],
    )
    assert argv[-1] == "hi; rm -rf /"
