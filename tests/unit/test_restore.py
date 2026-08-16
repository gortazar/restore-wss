import json

from restore_wss.model import Rect, Snapshot, Window
from restore_wss.plan import build_plan
from restore_wss.restore import execute


class FakeCore:
    """A compositor-side core that records what it was told and answers as instructed."""

    def __init__(self, launch_states=None, fail_place=False):
        self.calls = []
        self.workspaces = 1
        self.launch_states = launch_states or {}
        self.fail_place = fail_place
        self._next = 1

    def ensure_workspaces(self, count):
        self.calls.append(("ensure_workspaces", count))
        self.workspaces = max(self.workspaces, count)
        return self.workspaces

    def activate_workspace(self, index):
        self.calls.append(("activate_workspace", index))

    def place_window(self, window_id, placement_json):
        self.calls.append(("place_window", window_id, json.loads(placement_json)))
        if self.fail_place:
            raise RuntimeError("no window 77")
        return "{}"

    def launch_app(self, desktop_id, uris_json, placement_json):
        launch_id = f"launch-{self._next}"
        self._next += 1
        self.calls.append(
            ("launch_app", desktop_id, json.loads(uris_json), json.loads(placement_json))
        )
        return launch_id

    def get_launch_report(self, launch_id):
        state = self.launch_states.get(
            launch_id, {"state": "placed", "strategy": "app-id-and-timing", "window_id": "1"}
        )
        return json.dumps(state)


def a_window(**kwargs):
    defaults = dict(
        app_id="org.gnome.TextEditor.desktop",
        wm_class="org.gnome.TextEditor",
        title="notes.txt — Text Editor",
        workspace=2,
        monitor="eDP-1",
        frame=Rect(10, 20, 800, 600),
    )
    defaults.update(kwargs)
    return Window(**defaults)


def test_a_launch_is_carried_out_and_reported():
    plan = build_plan(Snapshot(windows=[a_window()]), [])
    core = FakeCore()
    result = execute(plan, core, wait_seconds=5, sleep=lambda _s: None)

    assert (
        "launch_app",
        "org.gnome.TextEditor.desktop",
        [],
        plan.actions[0].placement.to_json(),
    ) in core.calls
    assert [r.state for r in result.results] == ["done"]
    assert "app-id-and-timing" in result.results[0].detail


def test_enough_workspaces_are_created_before_anything_is_placed():
    plan = build_plan(Snapshot(windows=[a_window(workspace=3)]), [])
    core = FakeCore()
    execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    assert core.calls[0] == ("ensure_workspaces", 4)


def test_an_existing_window_is_moved():
    live = a_window(id="77", workspace=0)
    plan = build_plan(Snapshot(windows=[a_window()]), [live])
    core = FakeCore()
    execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    placed = [c for c in core.calls if c[0] == "place_window"]
    assert placed and placed[0][1] == "77"
    assert placed[0][2]["workspace"] == 2


def test_one_failure_does_not_stop_the_rest():
    plan = build_plan(
        Snapshot(
            windows=[
                a_window(id="77"),
                a_window(title="other", app_id="other.desktop", wm_class="Other"),
            ]
        ),
        [a_window(id="77")],
    )
    core = FakeCore(fail_place=True)
    result = execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    states = sorted(r.state for r in result.results)
    assert states == ["done", "failed"]
    assert "no window 77" in result.failures[0].detail


def test_a_launch_that_never_produces_a_window_is_reported_as_failed():
    plan = build_plan(Snapshot(windows=[a_window()]), [])
    core = FakeCore(launch_states={"launch-1": {"state": "timeout", "error": "no window in 90 s"}})
    result = execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    assert result.results[0].state == "failed"
    assert "no window in 90 s" in result.results[0].detail


def test_a_slow_launch_is_reported_as_pending_rather_than_failed():
    plan = build_plan(Snapshot(windows=[a_window()]), [])
    core = FakeCore(launch_states={"launch-1": {"state": "waiting"}})
    result = execute(plan, core, wait_seconds=1, sleep=lambda _s: None)
    assert result.results[0].state == "pending"


def test_an_application_that_refuses_its_size_is_still_a_success_but_says_so():
    plan = build_plan(Snapshot(windows=[a_window()]), [])
    core = FakeCore(
        launch_states={
            "launch-1": {
                "state": "placed",
                "strategy": "app-id-and-timing",
                "window_id": "5",
                "verdict": {
                    "state": "applied",
                    "size_honoured": False,
                    "note": "the application refused the size",
                },
            }
        }
    )
    result = execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    assert result.results[0].state == "done"
    assert "refused the size" in result.results[0].detail


def test_the_active_workspace_is_restored_last():
    plan = build_plan(Snapshot(windows=[a_window()], active_workspace=2, workspace_count=4), [])
    core = FakeCore()
    execute(plan, core, wait_seconds=5, sleep=lambda _s: None)
    assert core.calls[-1] == ("activate_workspace", 2)


def test_restoring_an_empty_snapshot_does_nothing():
    core = FakeCore()
    result = execute(build_plan(Snapshot(), []), core, wait_seconds=1, sleep=lambda _s: None)
    assert result.results == []
    assert not [c for c in core.calls if c[0] in ("launch_app", "place_window")]
