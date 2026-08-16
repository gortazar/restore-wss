"""``restore-wss-daemon`` — the process that keeps a current snapshot on disk at all times.

Two jobs, and it is worth being clear that the first is the whole point of the project:

**Capture.** The compositor-side core signals ``WindowsChanged``; the daemon re-reads the window
list, builds a snapshot and writes it. Writes are debounced (dragging a window emits a continuous
stream of changes) and rate-limited (an SSD and a battery are finite), so the guarantee is "the
snapshot is at most ``max_interval`` seconds stale", not "every keystroke is on disk".

**Answering.** ``org.gnome.RestoreWss`` for the CLI and, later, the review UI.

The daemon owns all the state and does all the I/O, because the other half of this program runs
*inside gnome-shell*, where a blocking write or a crash takes the desktop with it.
"""

from __future__ import annotations

import json
import time

from .busclient import ShellCoreClient
from .capture import (
    CaptureResult,
    apply_exclusions,
    capture,
    enrich_documents,
    enrich_terminals,
    read_boot_id,
)
from .cli import VERSION
from .config import Config, load_config
from .model import Snapshot
from .plan import RestorePlan, build_plan
from .protocol import API_VERSION, DAEMON_NAME, DAEMON_OBJECT_PATH
from .restore import execute
from .storage import SnapshotStore, default_state_dir
from .vpn import NetworkManager, VpnConnection, plan_vpn

#: How long to wait after the last change before writing. Long enough that a window drag is one
#: write, short enough that a crash a moment later still has the new layout.
DEBOUNCE_SECONDS = 2.0

#: A write happens at most this often even if the desktop is churning.
MIN_WRITE_INTERVAL_SECONDS = 10.0

#: How often to ask NetworkManager which VPNs are up. A snapshot is written every few seconds and
#: a VPN does not change that often, so the answer is cached in between.
VPN_POLL_SECONDS = 30.0


class Daemon:
    """Capture loop plus D-Bus surface. Separated from ``run()`` so tests can drive it."""

    def __init__(
        self,
        store: SnapshotStore | None = None,
        core: ShellCoreClient | None = None,
        *,
        config: Config | None = None,
        network_manager: NetworkManager | None = None,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.store = store if store is not None else SnapshotStore(default_state_dir())
        self.core = core
        self.config = config if config is not None else load_config()
        self._clock = clock
        self._wall_clock = wall_clock
        self._boot_id = read_boot_id()
        self._nm = network_manager
        self._vpn_cache: list[VpnConnection] = []
        self._vpn_checked = 0.0
        self._snapshot = Snapshot(boot_id=self._boot_id)
        self._skipped: dict[str, int] = {}
        self._last_write = 0.0
        self._pending = False

    # --- capture -------------------------------------------------------------------------

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    @property
    def core_available(self) -> bool:
        try:
            return self.core is not None and self.core.available
        except Exception:  # noqa: BLE001 — a dead proxy is "not available", not a crash
            return False

    def refresh(self) -> CaptureResult | None:
        """Re-read the compositor and update the in-memory snapshot. No disk I/O."""
        if not self.core_available:
            return None
        if self.config.paused:
            # Paused means paused: no window list, no process trees, no writes.
            return None
        result = capture(
            self.core.list_windows(),
            self.core.get_layout(),
            captured_at=self._wall_clock(),
            boot_id=self._boot_id,
        )
        apply_exclusions(result, self.config)
        enrich_terminals(result, self.config)
        enrich_documents(result, self.config)
        vpns = self._active_vpns()
        if vpns:
            result.snapshot.extra["vpn"] = [connection.to_json() for connection in vpns]
        self._snapshot = result.snapshot
        self._skipped = result.skipped
        return result

    def _active_vpns(self) -> list[VpnConnection]:
        """The VPNs that are up, cached: a snapshot is written every few seconds and a VPN does
        not change that often."""
        if self._nm is None:
            return self._vpn_cache
        now = self._clock()
        if not self._vpn_checked or now - self._vpn_checked > VPN_POLL_SECONDS:
            self._vpn_cache = self._nm.active_vpns()
            self._vpn_checked = now
        return self._vpn_cache

    def write(self) -> str:
        """Write the current snapshot now, whatever the rate limit says."""
        path = self.store.save(self._snapshot)
        self._last_write = self._clock()
        self._pending = False
        return str(path)

    def on_windows_changed(self) -> None:
        """Signal handler: mark the snapshot dirty. ``tick()`` decides when to write."""
        self._pending = True

    def tick(self, now: float | None = None) -> bool:
        """Called on the debounce timer. Writes if there is something to write. Returns whether
        it wrote."""
        now = self._clock() if now is None else now
        if not self._pending:
            return False
        if now - self._last_write < MIN_WRITE_INTERVAL_SECONDS and self._last_write:
            return False
        if self.refresh() is None:
            return False
        self.write()
        return True

    # --- D-Bus surface -------------------------------------------------------------------

    def handle_ping(self, message: str) -> str:
        return f"{API_VERSION} {VERSION} {message}"

    def handle_get_snapshot(self) -> str:
        self.refresh()
        return self._snapshot.dumps()

    def handle_save(self) -> str:
        self.refresh()
        return self.write()

    # --- restore -------------------------------------------------------------------------

    def build_restore_plan(self) -> RestorePlan:
        """What restoring the snapshot *on disk* would do to the desktop as it is now.

        On disk, not in memory: after a reboot the in-memory snapshot describes the empty desktop
        the daemon started against, and restoring that would be a very efficient way of doing
        nothing.
        """
        saved = self.store.load() or Snapshot()
        live = self.refresh()
        live_windows = live.snapshot.windows if live is not None else []
        monitors = [m.connector for m in (live.snapshot.monitors if live else []) if m.connector]
        plan = build_plan(
            saved,
            live_windows,
            available_monitors=monitors,
            command_policy=self.config.command_policy,
        )
        saved_vpns = [VpnConnection.from_json(raw) for raw in saved.extra.get("vpn", [])]
        if saved_vpns and self._nm is not None:
            plan.vpn = plan_vpn(
                saved_vpns,
                [connection.uuid for connection in self._nm.active_vpns()],
                self._nm.known_uuids(),
            )
        return plan

    def handle_plan_restore(self) -> str:
        plan = self.build_restore_plan()
        return json.dumps(_plan_to_json(plan))

    def handle_restore(self, only_json: str) -> str:
        plan = self.build_restore_plan()
        only = json.loads(only_json) if only_json else []
        if only:
            chosen = {int(index) for index in only}
            plan.actions = [a for i, a in enumerate(plan.actions) if i in chosen]
        if self.core is None:
            raise RuntimeError("the compositor-side core is not running; is the extension enabled?")
        result = execute(plan, self.core, network_manager=self._nm)
        return json.dumps(
            {
                "results": [
                    {
                        "state": r.state,
                        "detail": r.detail,
                        "description": r.action.describe(),
                        "kind": r.action.kind,
                    }
                    for r in result.results
                ],
                "vpn": [
                    {"name": name, "state": state, "detail": detail}
                    for name, state, detail in result.vpn
                ],
                "workspaces": result.workspaces_after,
            }
        )


def _plan_to_json(plan: RestorePlan) -> dict:
    return {
        "workspace_count": plan.workspace_count,
        "active_workspace": plan.active_workspace,
        "actions": [
            {
                "index": index,
                "kind": action.kind,
                "app_id": action.app_id,
                "window_id": action.window_id,
                "title": action.saved.title,
                "wm_class": action.saved.wm_class,
                "uris": action.uris,
                "placement": action.placement.to_json(),
                "tabs": [tab.to_json() for tab in action.tabs],
                "reason": action.reason,
                "confidence": action.confidence,
                "description": action.describe(),
            }
            for index, action in enumerate(plan.actions)
        ],
        "skipped": [
            {"title": window.title, "wm_class": window.wm_class, "reason": reason}
            for window, reason in plan.skipped
        ],
        "ambiguous": [
            {
                "title": match.saved.title,
                "candidate": match.candidate.title,
                "window_id": match.candidate.id,
                "score": match.score,
            }
            for match in plan.ambiguous
        ],
        "untouched": [{"title": w.title, "wm_class": w.wm_class} for w in plan.untouched],
        "vpn": [
            {"name": a.connection.name, "kind": a.kind, "description": a.describe()}
            for a in plan.vpn.actions
        ]
        + [
            {"name": c.name, "kind": "missing", "description": f"{c.name} is unknown here"}
            for c in plan.vpn.missing
        ],
    }


def run() -> int:
    """Run the daemon until killed. Returns a process exit code."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as error:
        print(
            "restore-wss: PyGObject is required to run the daemon "
            f"(python3-gi on Debian/Ubuntu): {error}"
        )
        return 1

    from .service import DaemonService

    core = None
    try:
        core = ShellCoreClient()
    except Exception as error:  # noqa: BLE001
        print(
            f"restore-wss: the compositor-side core is not reachable yet ({error}); waiting for it."
        )

    network_manager = NetworkManager()
    if not network_manager.available():
        print("restore-wss: NetworkManager is not reachable; VPNs will not be captured.")
        network_manager = None

    daemon = Daemon(core=core, network_manager=network_manager)
    service = DaemonService(daemon)

    loop = GLib.MainLoop()
    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION,
        DAEMON_NAME,
        Gio.BusNameOwnerFlags.NONE,
        lambda connection, _name: service.export(connection, DAEMON_OBJECT_PATH),
        None,
        lambda *_args: (print(f"restore-wss: could not own {DAEMON_NAME}"), loop.quit()),
    )

    if core is not None:
        try:
            core.connect_windows_changed(daemon.on_windows_changed)
            daemon.refresh()
        except Exception as error:  # noqa: BLE001
            print(f"restore-wss: could not subscribe to the compositor core: {error}")

    GLib.timeout_add_seconds(int(DEBOUNCE_SECONDS), lambda: (daemon.tick(), True)[1])

    # SIGTERM is how systemd stops a user unit, and SIGINT is how a developer does. Both should
    # leave the snapshot on disk current rather than a few seconds behind.
    def _quit():
        daemon.tick(now=float("inf"))
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 2, _quit)

    try:
        loop.run()
    finally:
        Gio.bus_unown_name(owner_id)
    return 0
