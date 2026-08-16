"""Which VPN was up, and getting it back.

NetworkManager only, per the answered open question in ``PLAN.md``: on this machine every VPN is
an NM connection, and supporting ``wg-quick``, Tailscale and stray ``openvpn`` processes as well
would be several times the work for no user.

What is stored is the connection's **identity** — its UUID, its name and its type — and never a
credential. Reactivation is ``ActivateConnection`` on a connection NetworkManager already knows;
if the secrets are in the keyring it just works, and if they are not, or the connection wants a
one-time code, that is a prompt for the user rather than something to retry blindly.

The split here is the same as everywhere else: :class:`NetworkManager` does the D-Bus, and the
decisions — what to reactivate, what to report — are pure functions tested against a fake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NM_NAME = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_ACTIVE_IFACE = "org.freedesktop.NetworkManager.Connection.Active"
NM_SETTINGS_CONNECTION_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

#: NM connection types that are a VPN as far as a user is concerned. ``wireguard`` is a normal
#: device type to NetworkManager but a VPN to the person using it.
VPN_TYPES = ("vpn", "wireguard")

CALL_TIMEOUT_MS = 10000


@dataclass
class VpnConnection:
    """One VPN connection, as recorded in the snapshot."""

    uuid: str = ""
    name: str = ""
    kind: str = "vpn"
    #: The VPN plugin, when NM reports one (``org.freedesktop.NetworkManager.openvpn``). Recorded
    #: for the report only — restore never uses it, because it activates by UUID.
    service: str = ""

    def to_json(self) -> dict[str, Any]:
        out = {"uuid": self.uuid, "name": self.name, "type": self.kind}
        if self.service:
            out["service"] = self.service
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> VpnConnection:
        return cls(
            uuid=str(raw.get("uuid", "")),
            name=str(raw.get("name", "")),
            kind=str(raw.get("type", "vpn")),
            service=str(raw.get("service", "")),
        )


@dataclass
class VpnAction:
    connection: VpnConnection
    #: ``activate`` or ``already-up``.
    kind: str = "activate"
    reason: str = ""

    def describe(self) -> str:
        if self.kind == "already-up":
            return f"vpn   {self.connection.name} is already connected"
        return f"vpn   reconnect {self.connection.name}"


@dataclass
class VpnPlan:
    actions: list[VpnAction] = field(default_factory=list)
    #: Connections in the snapshot that NetworkManager no longer knows about.
    missing: list[VpnConnection] = field(default_factory=list)

    def describe(self) -> list[str]:
        return [action.describe() for action in self.actions] + [
            f"vpn   {c.name} is no longer a NetworkManager connection on this machine"
            for c in self.missing
        ]


def plan_vpn(
    saved: list[VpnConnection],
    active_uuids: list[str],
    known_uuids: list[str],
) -> VpnPlan:
    """What restoring the saved VPNs would do.

    Idempotent like the rest of restore: a VPN that is already up is reported and left alone, not
    torn down and raised again.
    """
    plan = VpnPlan()
    for connection in saved:
        if connection.uuid in active_uuids:
            plan.actions.append(VpnAction(connection, "already-up"))
        elif connection.uuid in known_uuids:
            plan.actions.append(VpnAction(connection, "activate"))
        else:
            plan.missing.append(connection)
    return plan


def interpret_failure(error: str) -> str:
    """Turn NetworkManager's complaint into something worth showing a person.

    The distinction that matters is "this needs you" versus "this went wrong": a VPN asking for a
    password or a 2FA code is not a failure of the restore, and reporting it as one trains people
    to ignore the report.
    """
    lowered = error.lower()
    if "secret" in lowered or "password" in lowered or "no agents" in lowered:
        return (
            "NetworkManager needs a password or a code for this connection; "
            "connect it from the network menu"
        )
    if "not authorized" in lowered or "permission" in lowered:
        return "not authorised to activate this connection"
    if "unknown connection" in lowered:
        return "NetworkManager no longer has this connection"
    return error


class NetworkManager:
    """The D-Bus half. Everything that can fail because there is no NM lives here."""

    def __init__(self, bus=None):
        self._bus = bus

    @property
    def bus(self):
        if self._bus is None:
            from gi.repository import Gio

            self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        return self._bus

    def available(self) -> bool:
        try:
            self._get(NM_PATH, NM_IFACE, "Version")
            return True
        except Exception:  # noqa: BLE001 — no NetworkManager is a normal state, not an error
            return False

    def active_vpns(self) -> list[VpnConnection]:
        """The VPN connections that are up right now."""
        found: list[VpnConnection] = []
        try:
            paths = self._get(NM_PATH, NM_IFACE, "ActiveConnections")
        except Exception:  # noqa: BLE001
            return found
        for path in paths:
            try:
                kind = self._get(path, NM_ACTIVE_IFACE, "Type")
                if kind not in VPN_TYPES:
                    continue
                found.append(
                    VpnConnection(
                        uuid=self._get(path, NM_ACTIVE_IFACE, "Uuid"),
                        name=self._get(path, NM_ACTIVE_IFACE, "Id"),
                        kind=kind,
                    )
                )
            except Exception:  # noqa: BLE001 — a connection that vanished mid-read is not fatal
                continue
        return found

    def known_uuids(self) -> list[str]:
        """Every connection NetworkManager has settings for, by UUID."""
        from gi.repository import GLib

        try:
            reply = self.bus.call_sync(
                NM_NAME,
                "/org/freedesktop/NetworkManager/Settings",
                "org.freedesktop.NetworkManager.Settings",
                "ListConnections",
                None,
                GLib.VariantType("(ao)"),
                0,
                CALL_TIMEOUT_MS,
                None,
            )
        except Exception:  # noqa: BLE001
            return []
        uuids = []
        for path in reply.unpack()[0]:
            try:
                settings = self.bus.call_sync(
                    NM_NAME,
                    path,
                    NM_SETTINGS_CONNECTION_IFACE,
                    "GetSettings",
                    None,
                    GLib.VariantType("(a{sa{sv}})"),
                    0,
                    CALL_TIMEOUT_MS,
                    None,
                )
                uuid = settings.unpack()[0].get("connection", {}).get("uuid")
                if uuid:
                    uuids.append(uuid)
            except Exception:  # noqa: BLE001
                continue
        return uuids

    def connection_path(self, uuid: str) -> str | None:
        from gi.repository import GLib

        try:
            reply = self.bus.call_sync(
                NM_NAME,
                "/org/freedesktop/NetworkManager/Settings",
                "org.freedesktop.NetworkManager.Settings",
                "GetConnectionByUuid",
                GLib.Variant("(s)", (uuid,)),
                GLib.VariantType("(o)"),
                0,
                CALL_TIMEOUT_MS,
                None,
            )
        except Exception:  # noqa: BLE001
            return None
        return reply.unpack()[0]

    def activate(self, uuid: str) -> None:
        """Bring a connection up. Raises with NetworkManager's own message if it will not."""
        from gi.repository import GLib

        path = self.connection_path(uuid)
        if path is None:
            raise RuntimeError("unknown connection")
        self.bus.call_sync(
            NM_NAME,
            NM_PATH,
            NM_IFACE,
            "ActivateConnection",
            GLib.Variant("(ooo)", (path, "/", "/")),
            GLib.VariantType("(o)"),
            0,
            CALL_TIMEOUT_MS,
            None,
        )

    def _get(self, path: str, interface: str, name: str):
        from gi.repository import GLib

        reply = self.bus.call_sync(
            NM_NAME,
            path,
            PROPERTIES_IFACE,
            "Get",
            GLib.Variant("(ss)", (interface, name)),
            GLib.VariantType("(v)"),
            0,
            CALL_TIMEOUT_MS,
            None,
        )
        return reply.unpack()[0]
