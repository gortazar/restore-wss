import json

from restore_wss.capture import capture

LAYOUT = json.dumps(
    {
        "workspace_count": 3,
        "active_workspace": 1,
        "workspace_names": ["Writing", "Code", ""],
        "monitors": [
            {
                "index": 0,
                "connector": "eDP-1",
                "vendor": "AUO",
                "product": "B140",
                "serial": "0",
                "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "scale": 1.0,
                "primary": True,
            },
            {
                "index": 1,
                "connector": "DP-3",
                "vendor": "DEL",
                "product": "U2723QE",
                "serial": "ABC",
                "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440},
                "scale": 1.0,
                "primary": False,
            },
        ],
    }
)


def _windows(*records):
    return json.dumps(list(records))


def test_a_window_is_captured_with_its_placement():
    windows = _windows(
        {
            "id": "42",
            "wm_class": "Soffice",
            "title": "Thesis — LibreOffice Writer",
            "app_id": "libreoffice-writer.desktop",
            "pid": 1234,
            "workspace": 0,
            "monitor": 1,
            "frame": {"x": 1930, "y": 10, "width": 1200, "height": 900},
            "maximized": True,
            "stacking": 2,
        }
    )
    result = capture(windows, LAYOUT, captured_at=100.0, boot_id="boot-1")
    snapshot = result.snapshot

    assert snapshot.captured_at == 100.0
    assert snapshot.boot_id == "boot-1"
    assert snapshot.workspace_count == 3
    assert snapshot.active_workspace == 1
    assert [m.connector for m in snapshot.monitors] == ["eDP-1", "DP-3"]

    window = snapshot.windows[0]
    assert window.title == "Thesis — LibreOffice Writer"
    assert window.maximized
    assert window.frame.width == 1200
    # The index Meta reported has been resolved to the connector, which survives a replug.
    assert window.monitor == "DP-3"


def test_a_window_the_shell_has_not_identified_yet_is_skipped():
    """At window-created there is no wm_class and the app id is the synthetic window:N."""
    windows = _windows(
        {"id": "1", "wm_class": "", "app_id": "", "workspace": 0},
        {"id": "2", "wm_class": "", "app_id": "window:3", "workspace": 0},
        {"id": "3", "wm_class": "Foo", "app_id": "foo.desktop", "workspace": 0},
    )
    result = capture(windows, LAYOUT, captured_at=1.0)
    assert [w.id for w in result.snapshot.windows] == ["3"]
    assert result.skipped["not identified yet"] == 2


def test_zero_geometry_is_recorded_as_unknown_not_as_the_origin():
    windows = _windows(
        {
            "id": "1",
            "wm_class": "Foo",
            "app_id": "foo.desktop",
            "frame": {"x": 0, "y": 0, "width": 0, "height": 0},
        }
    )
    result = capture(windows, LAYOUT, captured_at=1.0)
    assert result.snapshot.windows[0].frame is None


def test_non_normal_windows_are_skipped():
    windows = _windows(
        {"id": "1", "wm_class": "Foo", "app_id": "foo.desktop", "window_type": "dock"},
        {"id": "2", "wm_class": "Bar", "app_id": "bar.desktop", "window_type": "normal"},
        {"id": "3", "wm_class": "Baz", "app_id": "baz.desktop", "skip_taskbar": True},
    )
    result = capture(windows, LAYOUT, captured_at=1.0)
    assert [w.id for w in result.snapshot.windows] == ["2"]
    assert result.skipped == {"window type dock": 1, "not in the taskbar": 1}


def test_an_empty_desktop_captures_an_empty_snapshot():
    result = capture("[]", LAYOUT, captured_at=1.0)
    assert result.snapshot.is_empty
    assert result.snapshot.workspace_count == 3


def test_a_window_on_an_unknown_monitor_index_gets_no_connector():
    windows = _windows({"id": "1", "wm_class": "Foo", "app_id": "foo.desktop", "monitor": 7})
    result = capture(windows, LAYOUT, captured_at=1.0)
    assert result.snapshot.windows[0].monitor == ""


def test_the_extension_may_send_the_connector_itself():
    windows = _windows(
        {"id": "1", "wm_class": "Foo", "app_id": "foo.desktop", "monitor_connector": "HDMI-2"}
    )
    result = capture(windows, LAYOUT, captured_at=1.0)
    assert result.snapshot.windows[0].monitor == "HDMI-2"
