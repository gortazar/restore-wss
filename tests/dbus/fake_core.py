"""A fake ``org.gnome.SessionCore``: the compositor half, without a compositor.

Everything the daemon asks the extension for is a JSON document, which is exactly why the protocol
was defined that way — a fake serving fixture JSON is indistinguishable from a real Shell as far as
the daemon is concerned, so capture, storage and the whole D-Bus surface can be tested on a private
bus with no desktop attached.

It runs as **its own process**, like the real extension does, and for a reason worth recording:
with the fake, the daemon and the test in one process, any synchronous D-Bus call deadlocks — the
caller blocks the thread that would have dispatched the reply. Three processes on a private bus is
both more faithful and simpler than making every call asynchronous.

    python tests/dbus/fake_core.py [scenario.json]

The scenario file, when given, is ``{"windows": [...], "layout": {...}}``. Tests drive it live
through ``org.gnome.SessionCore.Test``: ``SetWindows(json)`` and ``EmitChanged()``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from gi.repository import Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from restore_wss.protocol import (  # noqa: E402
    SHELL_IFACE_XML,
    SHELL_INTERFACE,
    SHELL_NAME,
    SHELL_OBJECT_PATH,
)

TEST_INTERFACE = "org.gnome.SessionCore.Test"

TEST_IFACE_XML = f"""
<node>
  <interface name="{TEST_INTERFACE}">
    <method name="SetWindows"><arg type="s" name="json" direction="in"/></method>
    <method name="EmitChanged"/>
    <method name="Quit"/>
  </interface>
</node>
"""

DEFAULT_LAYOUT = {
    "workspace_count": 2,
    "active_workspace": 0,
    "workspace_names": [],
    "monitors": [
        {
            "index": 0,
            "connector": "Meta-0",
            "vendor": "MetaVendor",
            "product": "MetaVirtualMonitor",
            "serial": "0x00",
            "geometry": {"x": 0, "y": 0, "width": 1280, "height": 800},
            "scale": 1.0,
            "primary": True,
        }
    ],
}

DEFAULT_WINDOWS = [
    {
        "id": "1",
        "wm_class": "Gnome-terminal",
        "title": "patxi@host: ~/git/my-repo",
        "app_id": "org.gnome.Terminal.desktop",
        "pid": 4242,
        "workspace": 0,
        "monitor": 0,
        "frame": {"x": 20, "y": 30, "width": 900, "height": 600},
        "maximized": False,
        "stacking": 0,
    }
]


class FakeCore:
    def __init__(self, windows=None, layout=None, version="fake-1"):
        self.windows = DEFAULT_WINDOWS if windows is None else windows
        self.layout = DEFAULT_LAYOUT if layout is None else layout
        self.version = version
        self.loop = GLib.MainLoop()
        self._connection = None
        self._ids: list[int] = []

    def run(self) -> None:
        Gio.bus_own_name(
            Gio.BusType.SESSION,
            SHELL_NAME,
            Gio.BusNameOwnerFlags.REPLACE,
            self._on_bus_acquired,
            None,
            lambda *_a: self.loop.quit(),
        )
        self.loop.run()

    def _on_bus_acquired(self, connection, _name):
        self._connection = connection
        for xml, interface, handler in (
            (SHELL_IFACE_XML, SHELL_INTERFACE, self._on_core_call),
            (TEST_IFACE_XML, TEST_INTERFACE, self._on_test_call),
        ):
            node = Gio.DBusNodeInfo.new_for_xml(xml)
            self._ids.append(
                connection.register_object(
                    SHELL_OBJECT_PATH, node.lookup_interface(interface), handler, None, None
                )
            )

    def _on_core_call(self, _conn, _sender, _path, _iface, method, parameters, invocation):
        if method == "Ping":
            reply = f"1 {self.version} {parameters.unpack()[0]}"
        elif method == "ListWindows":
            reply = json.dumps(self.windows)
        elif method == "GetLayout":
            reply = json.dumps(self.layout)
        else:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method
            )
            return
        invocation.return_value(GLib.Variant("(s)", (reply,)))

    def _on_test_call(self, _conn, _sender, _path, _iface, method, parameters, invocation):
        if method == "SetWindows":
            self.windows = json.loads(parameters.unpack()[0])
        elif method == "EmitChanged":
            self._connection.emit_signal(
                None, SHELL_OBJECT_PATH, SHELL_INTERFACE, "WindowsChanged", None
            )
        elif method == "Quit":
            GLib.idle_add(self.loop.quit)
        else:
            invocation.return_error_literal(
                Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, method
            )
            return
        invocation.return_value(None)


def main(argv: list[str]) -> int:
    windows = layout = None
    if argv:
        scenario = json.loads(Path(argv[0]).read_text())
        windows = scenario.get("windows")
        layout = scenario.get("layout")
    FakeCore(windows=windows, layout=layout).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
