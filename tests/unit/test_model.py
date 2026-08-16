from restore_wss.model import SCHEMA_VERSION, Monitor, Rect, Snapshot, Window


def test_empty_snapshot_round_trips():
    snap = Snapshot()
    assert snap.is_empty
    assert Snapshot.loads(snap.dumps()).to_json() == snap.to_json()
    assert snap.to_json()["schema"] == SCHEMA_VERSION


def test_window_round_trip_keeps_every_field():
    window = Window(
        id="763757057",
        wm_class="org.gnome.TextEditor",
        title="notes.txt — Text Editor",
        app_id="org.gnome.TextEditor.desktop",
        pid=4242,
        workspace=2,
        monitor="DP-1",
        frame=Rect(10, 20, 800, 600),
        maximized=True,
        stacking=3,
    )
    again = Window.from_json(window.to_json())
    assert again == window


def test_unknown_fields_survive_a_round_trip():
    """A snapshot written by a newer version, or annotated by hand, must not lose data."""
    raw = {
        "schema": 99,
        "windows": [{"id": "1", "wm_class": "Foo", "document": {"uri": "file:///thesis.odt"}}],
        "vpn": {"connection": "work"},
    }
    snap = Snapshot.from_json(raw)
    out = snap.to_json()
    assert out["vpn"] == {"connection": "work"}
    assert out["windows"][0]["document"] == {"uri": "file:///thesis.odt"}
    assert out["schema"] == 99


def test_zero_sized_geometry_is_reported_as_unknown():
    """The compositor reports 0x0 for up to a second after a window is created."""
    assert not Rect(0, 0, 0, 0).is_known
    assert Rect(0, 0, 640, 480).is_known


def test_missing_frame_stays_missing():
    window = Window.from_json({"id": "1"})
    assert window.frame is None
    assert "frame" not in window.to_json()


def test_monitor_identity_is_connector_plus_edid():
    monitor = Monitor(connector="DP-3", vendor="DEL", product="U2723QE", serial="ABC123")
    assert Monitor.from_json(monitor.to_json()) == monitor
