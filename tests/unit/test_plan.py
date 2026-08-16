from restore_wss.model import Rect, Snapshot, Window
from restore_wss.plan import LAUNCH, PLACE, build_plan


def saved_window(**kwargs):
    defaults = dict(
        app_id="org.gnome.TextEditor.desktop",
        wm_class="org.gnome.TextEditor",
        title="notes.txt — Text Editor",
        workspace=1,
        monitor="eDP-1",
        frame=Rect(10, 20, 800, 600),
    )
    defaults.update(kwargs)
    return Window(**defaults)


def snapshot_of(*windows, **kwargs):
    return Snapshot(windows=list(windows), **kwargs)


def test_an_empty_desktop_means_launching_everything():
    plan = build_plan(snapshot_of(saved_window()), [])
    assert [a.kind for a in plan.actions] == [LAUNCH]
    assert plan.actions[0].app_id == "org.gnome.TextEditor.desktop"
    assert plan.actions[0].placement.workspace == 1
    assert plan.actions[0].placement.frame.width == 800


def test_a_window_already_open_is_moved_not_launched_again():
    """Idempotency: restore adds what is missing and moves what is there."""
    live = saved_window(id="77", workspace=0, monitor="DP-1", frame=Rect(0, 0, 100, 100))
    plan = build_plan(snapshot_of(saved_window()), [live])
    assert [a.kind for a in plan.actions] == [PLACE]
    assert plan.actions[0].window_id == "77"
    assert plan.actions[0].placement.workspace == 1


def test_running_restore_twice_is_a_no_op_beyond_moving():
    snapshot = snapshot_of(saved_window())
    already_correct = saved_window(id="77")
    plan = build_plan(snapshot, [already_correct])
    assert all(a.kind == PLACE for a in plan.actions)
    assert not any(a.kind == LAUNCH for a in plan.actions)


def test_windows_the_snapshot_does_not_know_are_left_alone():
    stranger = Window(id="99", app_id="firefox.desktop", wm_class="firefox", title="Mail")
    plan = build_plan(snapshot_of(saved_window()), [stranger])
    assert [w.id for w in plan.untouched] == ["99"]
    assert all(a.window_id != "99" for a in plan.actions)


def test_a_window_with_no_application_id_is_skipped_with_a_reason():
    plan = build_plan(snapshot_of(saved_window(app_id="", wm_class="Weird")), [])
    assert plan.actions == []
    assert plan.skipped[0][1] == "no application id was captured"


def test_a_missing_monitor_keeps_the_workspace_and_drops_the_coordinates():
    plan = build_plan(
        snapshot_of(saved_window(monitor="DP-3")),
        [],
        available_monitors=["eDP-1"],
    )
    action = plan.actions[0]
    assert action.placement.workspace == 1
    assert action.placement.monitor is None
    assert action.placement.frame is None
    assert "not connected" in action.reason
    assert action.confidence < 1.0


def test_a_connected_monitor_is_kept():
    plan = build_plan(
        snapshot_of(saved_window(monitor="eDP-1")),
        [],
        available_monitors=["eDP-1", "DP-3"],
    )
    assert plan.actions[0].placement.monitor == "eDP-1"
    assert plan.actions[0].confidence == 1.0


def test_the_plan_asks_for_enough_workspaces():
    plan = build_plan(
        snapshot_of(saved_window(workspace=0), saved_window(workspace=4, title="other")),
        [],
    )
    assert plan.workspace_count == 5


def test_moves_come_before_launches():
    """The fast, certain part of a restore happens first."""
    open_now = saved_window(id="1", title="notes.txt — Text Editor")
    missing = saved_window(
        title="Thesis — LibreOffice Writer", app_id="writer.desktop", wm_class="Soffice"
    )
    plan = build_plan(snapshot_of(open_now, missing), [open_now])
    assert [a.kind for a in plan.actions] == [PLACE, LAUNCH]


def test_the_plan_can_describe_itself():
    plan = build_plan(snapshot_of(saved_window()), [])
    text = "\n".join(plan.describe())
    assert "start" in text
    assert "workspace 2" in text  # 1-based for humans


def test_an_ambiguous_match_is_reported_and_not_acted_on():
    saved = Window(
        app_id="org.gnome.Terminal.desktop",
        wm_class="Gnome-terminal",
        title="patxi@host: ~",
        workspace=0,
    )
    live = [
        Window(
            id="1",
            app_id="org.gnome.Terminal.desktop",
            wm_class="Gnome-terminal",
            title="patxi@host: ~/a",
        ),
        Window(
            id="2",
            app_id="org.gnome.Terminal.desktop",
            wm_class="Gnome-terminal",
            title="patxi@host: ~/b",
        ),
    ]
    plan = build_plan(snapshot_of(saved), live)
    assert plan.ambiguous
    assert all(a.kind != PLACE for a in plan.actions)
    assert "too close to call" in "\n".join(plan.describe())


def test_geometry_is_sent_relative_to_its_monitor_when_that_is_known():
    """A window on the right-hand screen should land in the same corner of it, wherever the
    screen is in the arrangement next time."""
    from restore_wss.model import Monitor

    snapshot = Snapshot(
        monitors=[
            Monitor(connector="eDP-1", geometry=Rect(0, 0, 1920, 1080)),
            Monitor(connector="DP-3", geometry=Rect(1920, 0, 2560, 1440)),
        ],
        windows=[saved_window(monitor="DP-3", frame=Rect(1970, 60, 800, 600))],
    )
    plan = build_plan(snapshot, [], available_monitors=["eDP-1", "DP-3"])
    placement = plan.actions[0].placement
    assert placement.frame_space == "monitor"
    assert (placement.frame.x, placement.frame.y) == (50, 60)
    assert placement.to_json()["frame_space"] == "monitor"


def test_geometry_stays_absolute_when_the_monitor_is_unknown():
    plan = build_plan(snapshot_of(saved_window(monitor="")), [])
    placement = plan.actions[0].placement
    assert placement.frame_space == "absolute"
    assert placement.frame.x == 10
