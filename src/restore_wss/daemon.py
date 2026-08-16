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

import time

from .busclient import ShellCoreClient
from .capture import CaptureResult, capture, read_boot_id
from .cli import VERSION
from .model import Snapshot
from .protocol import API_VERSION, DAEMON_NAME, DAEMON_OBJECT_PATH
from .storage import SnapshotStore, default_state_dir

#: How long to wait after the last change before writing. Long enough that a window drag is one
#: write, short enough that a crash a moment later still has the new layout.
DEBOUNCE_SECONDS = 2.0

#: A write happens at most this often even if the desktop is churning.
MIN_WRITE_INTERVAL_SECONDS = 10.0


class Daemon:
    """Capture loop plus D-Bus surface. Separated from ``run()`` so tests can drive it."""

    def __init__(
        self,
        store: SnapshotStore | None = None,
        core: ShellCoreClient | None = None,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.store = store if store is not None else SnapshotStore(default_state_dir())
        self.core = core
        self._clock = clock
        self._wall_clock = wall_clock
        self._boot_id = read_boot_id()
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
        result = capture(
            self.core.list_windows(),
            self.core.get_layout(),
            captured_at=self._wall_clock(),
            boot_id=self._boot_id,
        )
        self._snapshot = result.snapshot
        self._skipped = result.skipped
        return result

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

    daemon = Daemon(core=core)
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
