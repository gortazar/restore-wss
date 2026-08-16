"""The daemon's D-Bus surface, against a fake compositor core, on a private bus.

Three processes: this test, a fake ``org.gnome.SessionCore`` and a real daemon — the same shape as
the real thing, and the only shape in which synchronous calls do not deadlock (see
``fake_core.py``). Run under ``tools/with-session-bus.sh``, which is what ``make test-dbus`` and
``nix flake check`` do.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from restore_wss.busclient import DaemonClient  # noqa: E402
from restore_wss.protocol import DAEMON_NAME, SHELL_NAME, SHELL_OBJECT_PATH  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEST_INTERFACE = "org.gnome.SessionCore.Test"


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def _name_has_owner(name: str) -> bool:
    reply = _bus().call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        GLib.Variant("(s)", (name,)),
        GLib.VariantType("(b)"),
        Gio.DBusCallFlags.NONE,
        2000,
        None,
    )
    return reply.unpack()[0]


def _wait_for_name(name: str, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _name_has_owner(name):
            return
        time.sleep(0.05)
    raise AssertionError(f"{name} never appeared on the bus")


def _core_control(method: str, *args):
    signature = "(s)" if args else "()"
    _bus().call_sync(
        SHELL_NAME,
        SHELL_OBJECT_PATH,
        TEST_INTERFACE,
        method,
        GLib.Variant(signature, tuple(args)),
        None,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    )


def _spawn(args, env):
    process = subprocess.Popen(args, env=env, cwd=REPO)
    return process


@pytest.fixture
def environment(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["RESTORE_WSS_HOME"] = str(tmp_path / "home")
    return env


@pytest.fixture
def core(environment):
    process = _spawn([sys.executable, str(HERE / "fake_core.py")], environment)
    _wait_for_name(SHELL_NAME)
    yield process
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def daemon(core, environment, tmp_path):
    process = _spawn([sys.executable, "-m", "restore_wss", "daemon"], environment)
    _wait_for_name(DAEMON_NAME)
    yield process
    process.terminate()
    process.wait(timeout=10)


def _state_dir(tmp_path) -> Path:
    return tmp_path / "home" / "state"


def test_ping_announces_the_api_version(daemon):
    reply = DaemonClient().ping("hello")
    assert reply.startswith("1 ")
    assert reply.endswith(" hello")


def test_the_daemon_reports_the_windows_the_core_lists(daemon):
    snapshot = DaemonClient().get_snapshot()
    assert snapshot is not None
    assert [w.wm_class for w in snapshot.windows] == ["Gnome-terminal"]
    # The monitor index the compositor reports has been resolved to a connector name, which is
    # what survives a replug.
    assert snapshot.windows[0].monitor == "Meta-0"


def test_save_writes_a_snapshot_that_can_be_read_back(daemon, tmp_path):
    path = Path(DaemonClient().save())
    assert path == _state_dir(tmp_path) / "session.json"
    written = json.loads(path.read_text())
    assert written["windows"][0]["title"] == "patxi@host: ~/git/my-repo"
    assert written["schema"] == 1


def test_the_state_directory_the_daemon_creates_is_private(daemon, tmp_path):
    DaemonClient().save()
    assert oct(os.stat(_state_dir(tmp_path)).st_mode)[-3:] == "700"


def test_a_change_in_the_compositor_leads_to_a_write_nobody_asked_for(daemon, tmp_path):
    """The whole point of the daemon: the snapshot follows the desktop unprompted."""
    DaemonClient().save()  # a known starting point

    moved = json.dumps(
        [
            {
                "id": "1",
                "wm_class": "Gnome-terminal",
                "title": "patxi@host: ~/git/other-repo",
                "app_id": "org.gnome.Terminal.desktop",
                "pid": 4242,
                "workspace": 1,
                "monitor": 0,
                "frame": {"x": 20, "y": 30, "width": 900, "height": 600},
            }
        ]
    )
    _core_control("SetWindows", moved)
    _core_control("EmitChanged")

    target = _state_dir(tmp_path) / "session.json"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        written = json.loads(target.read_text())
        if written["windows"][0]["title"] == "patxi@host: ~/git/other-repo":
            assert written["windows"][0]["workspace"] == 1
            return
        time.sleep(0.2)
    raise AssertionError("the daemon never wrote the change on its own")


def test_the_previous_generation_is_kept_alongside(daemon, tmp_path):
    DaemonClient().save()
    _core_control("SetWindows", "[]")
    DaemonClient().save()
    state = _state_dir(tmp_path)
    assert (state / "session.prev.json").exists()
    assert json.loads((state / "session.json").read_text())["windows"] == []
    assert json.loads((state / "session.prev.json").read_text())["windows"]


def _core_activity() -> dict:
    reply = _bus().call_sync(
        SHELL_NAME,
        SHELL_OBJECT_PATH,
        TEST_INTERFACE,
        "GetActivity",
        None,
        GLib.VariantType("(s)"),
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    )
    return json.loads(reply.unpack()[0])


def test_planning_a_restore_of_the_snapshot_on_disk(daemon):
    """The plan is computed against the snapshot on disk, not the live one: after a reboot the
    live desktop is empty and the file is the only record of what was lost."""
    DaemonClient().save()  # the terminal window the fake core reports is now "the session"
    _core_control("SetWindows", "[]")  # ... and now the desktop is empty, as after a reboot

    plan = DaemonClient().plan_restore()
    assert [a["kind"] for a in plan["actions"]] == ["launch"]
    assert plan["actions"][0]["app_id"] == "org.gnome.Terminal.desktop"
    assert plan["actions"][0]["placement"]["workspace"] == 0


def test_restoring_launches_what_is_missing(daemon):
    DaemonClient().save()
    _core_control("SetWindows", "[]")

    result = DaemonClient().restore()
    assert [r["state"] for r in result["results"]] == ["done"]

    activity = _core_activity()
    assert activity["launched"][0][0] == "org.gnome.Terminal.desktop"
    assert activity["workspaces"] >= 1


def test_restoring_when_the_window_is_already_open_moves_it_instead(daemon):
    """Idempotency over the bus: the same window, still open, is moved rather than launched."""
    DaemonClient().save()
    moved = json.dumps(
        [
            {
                "id": "1",
                "wm_class": "Gnome-terminal",
                "title": "patxi@host: ~/git/my-repo",
                "app_id": "org.gnome.Terminal.desktop",
                "pid": 4242,
                "workspace": 1,
                "monitor": 0,
                "frame": {"x": 0, "y": 0, "width": 400, "height": 300},
            }
        ]
    )
    _core_control("SetWindows", moved)

    result = DaemonClient().restore()
    assert [r["state"] for r in result["results"]] == ["done"]
    activity = _core_activity()
    assert not activity["launched"]
    window_id, placement = activity["placed"][0]
    assert window_id == "1"
    assert placement["workspace"] == 0  # back to where the snapshot remembers it
