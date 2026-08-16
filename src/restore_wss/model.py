"""The snapshot: what a session looked like, as plain data.

Deliberately dependency-free and free of behaviour beyond (de)serialisation, so that every rule
about what a snapshot *means* — matching, policy, confidence — is testable against fixtures
without a desktop, a bus or a daemon.

The on-disk format is documented in ``docs/state-schema.md``; this module is its reference
implementation. Two rules hold everywhere:

* **Unknown fields survive a round trip.** A snapshot written by a newer version, hand-edited by
  the user, or extended by an app adapter must not lose data by being read here.
* **Missing fields have honest defaults.** A window with no recorded geometry is a window whose
  geometry is unknown, never a window at the origin (the compositor reports ``0x0`` for over a
  second after a window is created — see ``docs/platform-findings.md``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Bumped when a snapshot written by this version cannot be read by the previous one.
#: ``docs/state-schema.md`` carries the migration table.
SCHEMA_VERSION = 1


@dataclass
class Rect:
    """A frame rectangle in compositor coordinates."""

    x: int
    y: int
    width: int
    height: int

    def to_json(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Rect:
        return cls(
            x=int(raw.get("x", 0)),
            y=int(raw.get("y", 0)),
            width=int(raw.get("width", 0)),
            height=int(raw.get("height", 0)),
        )

    @property
    def is_known(self) -> bool:
        """A zero-sized rect means "the client had not committed a buffer yet", not a real size."""
        return self.width > 0 and self.height > 0


@dataclass
class Window:
    """One window, as the compositor saw it and as restore will try to recreate it."""

    #: Per-session compositor handle. Recorded for correlation *within* a run only: it cannot
    #: survive a reboot, which is why matching is heuristic (docs/similar-tools.md §3).
    id: str = ""
    wm_class: str = ""
    title: str = ""
    #: Desktop-file id, e.g. "org.gnome.TextEditor.desktop". The tier-0 answer to "what do I
    #: launch to get this window back".
    app_id: str = ""
    pid: int = 0
    workspace: int = 0
    #: Connector name ("DP-1"), not a monitor index: indices renumber on replug.
    monitor: str = ""
    frame: Rect | None = None
    maximized: bool = False
    fullscreen: bool = False
    minimized: bool = False
    #: Position in the stack, 0 = bottom. Best-effort; see docs/limitations.md.
    stacking: int = 0
    #: Reserved for xdg-session-management-v1: when the app restores itself on a compositor that
    #: supports it, restore-wss records that fact here and leaves the window alone. Empty on
    #: GNOME 46, which has no such protocol (docs/platform-findings.md §1).
    session_protocol: str = ""
    #: Anything a newer version or an app adapter wrote that this version does not model.
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "id",
        "wm_class",
        "title",
        "app_id",
        "pid",
        "workspace",
        "monitor",
        "frame",
        "maximized",
        "fullscreen",
        "minimized",
        "stacking",
        "session_protocol",
    }

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "wm_class": self.wm_class,
            "title": self.title,
            "app_id": self.app_id,
            "pid": self.pid,
            "workspace": self.workspace,
            "monitor": self.monitor,
            "maximized": self.maximized,
            "fullscreen": self.fullscreen,
            "minimized": self.minimized,
            "stacking": self.stacking,
        }
        if self.frame is not None:
            out["frame"] = self.frame.to_json()
        if self.session_protocol:
            out["session_protocol"] = self.session_protocol
        out.update(self.extra)
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Window:
        frame = raw.get("frame")
        return cls(
            id=str(raw.get("id", "")),
            wm_class=str(raw.get("wm_class", "")),
            title=str(raw.get("title", "")),
            app_id=str(raw.get("app_id", "")),
            pid=int(raw.get("pid", 0) or 0),
            workspace=int(raw.get("workspace", 0) or 0),
            monitor=str(raw.get("monitor", "")),
            frame=Rect.from_json(frame) if isinstance(frame, dict) else None,
            maximized=bool(raw.get("maximized", False)),
            fullscreen=bool(raw.get("fullscreen", False)),
            minimized=bool(raw.get("minimized", False)),
            stacking=int(raw.get("stacking", 0) or 0),
            session_protocol=str(raw.get("session_protocol", "")),
            extra={k: v for k, v in raw.items() if k not in cls._KNOWN},
        )


@dataclass
class Monitor:
    """A monitor, identified the only way that survives a replug."""

    connector: str = ""
    #: EDID triple, when DisplayConfig knows it. Together with the connector this is what tells
    #: "the same external screen, plugged into a different port" from "a different screen".
    vendor: str = ""
    product: str = ""
    serial: str = ""
    geometry: Rect | None = None
    scale: float = 1.0
    primary: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "connector": self.connector,
            "vendor": self.vendor,
            "product": self.product,
            "serial": self.serial,
            "scale": self.scale,
            "primary": self.primary,
        }
        if self.geometry is not None:
            out["geometry"] = self.geometry.to_json()
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Monitor:
        geometry = raw.get("geometry")
        return cls(
            connector=str(raw.get("connector", "")),
            vendor=str(raw.get("vendor", "")),
            product=str(raw.get("product", "")),
            serial=str(raw.get("serial", "")),
            geometry=Rect.from_json(geometry) if isinstance(geometry, dict) else None,
            scale=float(raw.get("scale", 1.0) or 1.0),
            primary=bool(raw.get("primary", False)),
        )


@dataclass
class Snapshot:
    """A whole session at one instant."""

    schema: int = SCHEMA_VERSION
    #: Unix time of the capture this snapshot describes.
    captured_at: float = 0.0
    #: /proc/sys/kernel/random/boot_id at capture time. Restore compares it with the current boot
    #: id to tell "we rebooted, this snapshot describes the session that was lost" from "the user
    #: logged out and back in", which is what makes an automatic restore offer non-spurious.
    boot_id: str = ""
    workspace_count: int = 0
    active_workspace: int = 0
    #: Workspace names, when the user has set any. Best-effort, per the answered open question.
    workspace_names: list[str] = field(default_factory=list)
    monitors: list[Monitor] = field(default_factory=list)
    windows: list[Window] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    _KNOWN = {
        "schema",
        "captured_at",
        "boot_id",
        "workspace_count",
        "active_workspace",
        "workspace_names",
        "monitors",
        "windows",
    }

    @property
    def is_empty(self) -> bool:
        return not self.windows

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": self.schema,
            "captured_at": self.captured_at,
            "boot_id": self.boot_id,
            "workspace_count": self.workspace_count,
            "active_workspace": self.active_workspace,
            "workspace_names": list(self.workspace_names),
            "monitors": [m.to_json() for m in self.monitors],
            "windows": [w.to_json() for w in self.windows],
        }
        out.update(self.extra)
        return out

    def dumps(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_json(), indent=indent, sort_keys=False)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Snapshot:
        return cls(
            schema=int(raw.get("schema", SCHEMA_VERSION) or SCHEMA_VERSION),
            captured_at=float(raw.get("captured_at", 0.0) or 0.0),
            boot_id=str(raw.get("boot_id", "")),
            workspace_count=int(raw.get("workspace_count", 0) or 0),
            active_workspace=int(raw.get("active_workspace", 0) or 0),
            workspace_names=[str(n) for n in raw.get("workspace_names", []) or []],
            monitors=[Monitor.from_json(m) for m in raw.get("monitors", []) or []],
            windows=[Window.from_json(w) for w in raw.get("windows", []) or []],
            extra={k: v for k, v in raw.items() if k not in cls._KNOWN},
        )

    @classmethod
    def loads(cls, text: str) -> Snapshot:
        return cls.from_json(json.loads(text))
