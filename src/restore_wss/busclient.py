"""Talking to the other process, in both directions.

Both clients are thin wrappers over ``Gio.DBusProxy``. They live together because the two halves
of the protocol are two halves of one contract, and because keeping every PyGObject import in one
place means the pure-logic modules stay importable on a machine with no desktop.
"""

from __future__ import annotations

from .model import Snapshot
from .protocol import (
    DAEMON_INTERFACE,
    DAEMON_NAME,
    DAEMON_OBJECT_PATH,
    SHELL_INTERFACE,
    SHELL_NAME,
    SHELL_OBJECT_PATH,
)

# Cold application starts have been measured at ~30 s in a headless session, but every call here
# is a question about state the other side already has, so a short timeout is right: a slow answer
# means the compositor is wedged, and blocking the daemon on it helps nobody.
CALL_TIMEOUT_MS = 5000


def _proxy(name: str, path: str, interface: str):
    from gi.repository import Gio

    return Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION,
        Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        name,
        path,
        interface,
        None,
    )


class ShellCoreClient:
    """The daemon's handle on the compositor-side core (the extension)."""

    def __init__(self, proxy=None):
        self._proxy = (
            proxy if proxy is not None else _proxy(SHELL_NAME, SHELL_OBJECT_PATH, SHELL_INTERFACE)
        )

    @property
    def available(self) -> bool:
        """False when the extension is not running — the normal state on a fresh install."""
        return self._proxy.get_name_owner() is not None

    def ping(self, message: str = "hello") -> str:
        return self._call("Ping", "(s)", (message,))

    def list_windows(self) -> str:
        return self._call("ListWindows", "()", ())

    def get_layout(self) -> str:
        return self._call("GetLayout", "()", ())

    def ensure_workspaces(self, count: int) -> int:
        from gi.repository import GLib

        result = self._proxy.call_sync(
            "EnsureWorkspaces", GLib.Variant("(u)", (count,)), 0, CALL_TIMEOUT_MS, None
        )
        return result.unpack()[0]

    def activate_workspace(self, index: int) -> None:
        from gi.repository import GLib

        self._proxy.call_sync(
            "ActivateWorkspace", GLib.Variant("(u)", (index,)), 0, CALL_TIMEOUT_MS, None
        )

    def place_window(self, window_id: str, placement_json: str) -> str:
        return self._call("PlaceWindow", "(ss)", (window_id, placement_json))

    def placement_verdict(self, window_id: str, requested_json: str) -> str:
        return self._call("GetPlacementVerdict", "(ss)", (window_id, requested_json))

    def launch_app(self, desktop_id: str, uris_json: str, placement_json: str) -> str:
        return self._call("LaunchApp", "(sss)", (desktop_id, uris_json, placement_json))

    def expect_window(self, desktop_id: str, placement_json: str) -> str:
        return self._call("ExpectWindow", "(ss)", (desktop_id, placement_json))

    def get_launch_report(self, launch_id: str) -> str:
        return self._call("GetLaunchReport", "(s)", (launch_id,))

    def connect_windows_changed(self, callback) -> int:
        """Subscribe to the coalesced change signal. Returns the handler id."""

        def _on_signal(_proxy, _sender, signal, _params):
            if signal == "WindowsChanged":
                callback()

        return self._proxy.connect("g-signal", _on_signal)

    def _call(self, method: str, signature: str, args: tuple) -> str:
        from gi.repository import GLib

        result = self._proxy.call_sync(
            method, GLib.Variant(signature, args), 0, CALL_TIMEOUT_MS, None
        )
        return result.unpack()[0]


class DaemonClient:
    """The CLI's handle on the daemon."""

    def __init__(self, proxy=None):
        self._proxy = (
            proxy
            if proxy is not None
            else _proxy(DAEMON_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE)
        )

    @property
    def available(self) -> bool:
        return self._proxy.get_name_owner() is not None

    def ping(self, message: str = "hello") -> str:
        return self._call("Ping", "(s)", (message,))

    def get_snapshot(self) -> Snapshot | None:
        if not self.available:
            return None
        return Snapshot.loads(self._call("GetSnapshot", "()", ()))

    def save(self) -> str:
        return self._call("Save", "()", ())

    def plan_restore(self) -> dict:
        import json

        return json.loads(self._call("PlanRestore", "()", ()))

    def restore(self, only: list[int] | None = None) -> dict:
        import json

        return json.loads(self._call("Restore", "(s)", (json.dumps(only or []),)))

    def _call(self, method: str, signature: str, args: tuple) -> str:
        from gi.repository import GLib

        result = self._proxy.call_sync(
            method, GLib.Variant(signature, args), 0, CALL_TIMEOUT_MS, None
        )
        return result.unpack()[0]
