"""Exporting the daemon on the session bus.

PyGObject has no equivalent of GJS's ``Gio.DBusExportedObject.wrapJSObject``, so the dispatch is
written out: parse the interface XML once, register the object, and map an incoming method name to
a ``Daemon`` method. Keeping it in its own module means ``Daemon`` itself has no D-Bus types in it
and can be tested by calling its methods directly.
"""

from __future__ import annotations

from .protocol import DAEMON_IFACE_XML, DAEMON_INTERFACE


class DaemonService:
    def __init__(self, daemon):
        self._daemon = daemon
        self._registration_id = 0
        self._connection = None

    def export(self, connection, object_path: str) -> None:
        from gi.repository import Gio

        node = Gio.DBusNodeInfo.new_for_xml(DAEMON_IFACE_XML)
        interface = node.lookup_interface(DAEMON_INTERFACE)
        self._connection = connection
        self._registration_id = connection.register_object(
            object_path,
            interface,
            self._on_method_call,
            self._on_get_property,
            None,
        )

    def unexport(self) -> None:
        if self._connection is not None and self._registration_id:
            self._connection.unregister_object(self._registration_id)
            self._registration_id = 0

    # Gio calls these on the main loop thread.

    def _on_method_call(
        self, _connection, _sender, _path, _interface, method, parameters, invocation
    ):
        from gi.repository import GLib

        try:
            if method == "Ping":
                reply = self._daemon.handle_ping(parameters.unpack()[0])
            elif method == "GetSnapshot":
                reply = self._daemon.handle_get_snapshot()
            elif method == "Save":
                reply = self._daemon.handle_save()
            else:
                invocation.return_error_literal(
                    Gio.DBusError.quark(), Gio.DBusError.UNKNOWN_METHOD, f"no method {method}"
                )
                return
        except Exception as error:  # noqa: BLE001 — a D-Bus error is the right way to report it
            invocation.return_error_literal(
                GLib.quark_from_string("org.gnome.RestoreWss.Error"), 0, str(error)
            )
            return

        invocation.return_value(GLib.Variant("(s)", (reply,)))

    def _on_get_property(self, _connection, _sender, _path, _interface, name):
        from gi.repository import GLib

        if name == "Capturing":
            return GLib.Variant("b", self._daemon.core_available)
        if name == "ShellCoreVersion":
            if not self._daemon.core_available:
                return GLib.Variant("s", "")
            try:
                return GLib.Variant("s", self._daemon.core.ping("version"))
            except Exception:  # noqa: BLE001
                return GLib.Variant("s", "")
        return None


# Imported lazily above; kept here so the module still imports without PyGObject for tooling.
try:  # pragma: no cover - import-time environment detail
    from gi.repository import Gio
except (ImportError, ValueError):  # pragma: no cover
    Gio = None
