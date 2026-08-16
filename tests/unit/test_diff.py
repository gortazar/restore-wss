"""`restore-wss diff`: the snapshot versus what is running."""

from restore_wss.diff import diff_windows
from restore_wss.model import Window


def w(title, workspace=0, monitor="eDP-1", **kwargs):
    return Window(
        app_id="org.gnome.TextEditor.desktop",
        wm_class="org.gnome.TextEditor",
        title=title,
        workspace=workspace,
        monitor=monitor,
        **kwargs,
    )


def test_an_identical_desktop_shows_no_difference():
    saved = [w("notes.txt")]
    difference = diff_windows(saved, [w("notes.txt", id="1")])
    assert difference.is_empty
    assert difference.unchanged == 1


def test_a_window_that_is_not_running_is_reported_as_missing():
    difference = diff_windows([w("notes.txt")], [])
    assert [x.title for x in difference.only_in_snapshot] == ["notes.txt"]
    assert "- notes.txt (not running)" in difference.describe()


def test_a_window_the_snapshot_does_not_know_is_reported_as_extra():
    difference = diff_windows([], [w("scratch", id="9")])
    assert [x.title for x in difference.only_running] == ["scratch"]
    assert "+ scratch (not in the snapshot)" in difference.describe()


def test_a_window_on_the_wrong_workspace_is_reported_as_moved():
    difference = diff_windows([w("notes.txt", workspace=2)], [w("notes.txt", workspace=0, id="1")])
    assert len(difference.moved) == 1
    assert "workspace 1 now, 3 in the snapshot" in difference.describe()[0]


def test_a_window_on_the_wrong_monitor_counts_as_moved():
    difference = diff_windows(
        [w("notes.txt", monitor="DP-3")], [w("notes.txt", monitor="eDP-1", id="1")]
    )
    assert difference.moved
    assert difference.unchanged == 0
