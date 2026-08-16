// org.gnome.SessionCore — the compositor-side service.
//
// It observes (ListWindows, GetLayout, the WindowsChanged signal) and it acts on instructions
// (EnsureWorkspaces, PlaceWindow, LaunchApp). What it does not do is decide anything: no policy,
// no state worth losing, no files, no processes of its own. This code runs inside gnome-shell,
// where a stall freezes the desktop and an exception can take the session down, so everything
// that can live in the daemon does.
//
// That split is also what makes this half reusable: the interface is about windows and monitors,
// not about restoring a session after a reboot (see docs/shared-core.md).

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

import {
    API_VERSION,
    SHELL_IFACE_XML,
    SHELL_NAME,
    SHELL_OBJECT_PATH,
} from './protocol.js';
import { MonitorConnectors, describeWorkspaces } from './monitors.js';
import {
    Launcher,
    ensureWorkspaces,
    findWindow,
    placeWindow,
    placementVerdict,
} from './placement.js';
import { introspectAllWindows } from './windowIntrospect.js';

// Dragging a window emits position-changed continuously. One D-Bus message per motion event would
// be absurd, so changes are coalesced here; the daemon debounces its writes again on top.
const CHANGE_DEBOUNCE_MS = 400;

export class SessionCore {
    constructor(version) {
        this._version = version;
        this._impl = Gio.DBusExportedObject.wrapJSObject(SHELL_IFACE_XML, this);
        this._ownerId = 0;
        this._debounceId = 0;
        this._monitors = null;
        this._launcher = null;
        this._signalIds = [];
        this._windowSignals = new Map();
    }

    enable() {
        this._monitors = new MonitorConnectors();
        this._launcher = new Launcher(this._monitors);
        this._impl.export(Gio.DBus.session, SHELL_OBJECT_PATH);
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION, SHELL_NAME, Gio.BusNameOwnerFlags.REPLACE, null, null,
            () => console.warn(`restore-wss: could not own ${SHELL_NAME}`));
        this._watchDisplay();
    }

    disable() {
        this._unwatchDisplay();
        if (this._debounceId) {
            GLib.source_remove(this._debounceId);
            this._debounceId = 0;
        }
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
        this._impl.unexport();
        this._launcher?.destroy();
        this._launcher = null;
        this._monitors?.destroy();
        this._monitors = null;
    }

    // --- D-Bus methods ------------------------------------------------------------------

    Ping(message) {
        return `${API_VERSION} ${this._version} ${message}`;
    }

    ListWindows() {
        const windows = introspectAllWindows(global.display).map(window => ({
            ...window,
            // Resolved here as well as in the daemon: the extension is the only side that can ask
            // DisplayConfig cheaply, and a window whose monitor has no connector is worth knowing
            // about at capture time rather than at restore time.
            monitor_connector: this._monitors?.connectorFor(window.monitor) ?? '',
        }));
        return JSON.stringify(windows);
    }

    GetLayout() {
        return JSON.stringify({
            ...describeWorkspaces(),
            monitors: this._monitors?.describe() ?? [],
        });
    }

    EnsureWorkspaces(count) {
        return ensureWorkspaces(count);
    }

    ActivateWorkspace(index) {
        const workspace = global.workspace_manager.get_workspace_by_index(index);
        workspace?.activate(global.get_current_time());
    }

    PlaceWindow(windowId, placementJson) {
        const window = findWindow(windowId);
        if (!window)
            throw new Error(`no window ${windowId}`);
        const requested = placeWindow(window, JSON.parse(placementJson), this._monitors);
        return JSON.stringify(requested);
    }

    GetPlacementVerdict(windowId, requestedJson) {
        const requested = requestedJson ? JSON.parse(requestedJson) : {};
        return JSON.stringify(placementVerdict(findWindow(windowId), requested));
    }

    LaunchApp(desktopId, urisJson, placementJson) {
        const uris = urisJson ? JSON.parse(urisJson) : [];
        const placement = placementJson ? JSON.parse(placementJson) : {};
        return this._launcher.launch(desktopId, uris, placement);
    }

    GetLaunchReport(launchId) {
        return JSON.stringify(this._launcher.report(launchId));
    }

    // --- change tracking ----------------------------------------------------------------

    _watchDisplay() {
        const display = global.display;
        const manager = global.workspace_manager;

        this._connect(display, 'window-created', (_d, window) => {
            this._watchWindow(window);
            this._changed();
        });
        this._connect(display, 'grab-op-end', () => this._changed());
        this._connect(manager, 'active-workspace-changed', () => this._changed());
        this._connect(manager, 'workspace-added', () => this._changed());
        this._connect(manager, 'workspace-removed', () => this._changed());
        this._connect(Main.layoutManager, 'monitors-changed', () => this._changed());

        for (const actor of global.get_window_actors())
            this._watchWindow(actor.meta_window);
    }

    _connect(object, signal, callback) {
        try {
            this._signalIds.push([object, object.connect(signal, callback)]);
        } catch (error) {
            // A signal that does not exist on this Shell version is a fact to log, not a crash:
            // the rest of the capture still works.
            console.warn(`restore-wss: cannot watch ${signal}: ${error}`);
        }
    }

    _watchWindow(window) {
        if (!window || this._windowSignals.has(window))
            return;
        const ids = [
            // Geometry arrives late and unpredictably (52–1325 ms after creation, measured), so
            // capture is signal-driven rather than timed.
            window.connect('position-changed', () => this._changed()),
            window.connect('size-changed', () => this._changed()),
            window.connect('workspace-changed', () => this._changed()),
            window.connect('notify::title', () => this._changed()),
            window.connect('notify::wm-class', () => this._changed()),
            window.connect('notify::minimized', () => this._changed()),
            window.connect('unmanaging', () => {
                this._unwatchWindow(window);
                this._changed();
            }),
        ];
        this._windowSignals.set(window, ids);
    }

    _unwatchWindow(window) {
        const ids = this._windowSignals.get(window);
        if (!ids)
            return;
        for (const id of ids) {
            try {
                window.disconnect(id);
            } catch {
                // The window is going away; a failed disconnect is not interesting.
            }
        }
        this._windowSignals.delete(window);
    }

    _unwatchDisplay() {
        for (const [object, id] of this._signalIds) {
            try {
                object.disconnect(id);
            } catch {
                // Same as above.
            }
        }
        this._signalIds = [];
        for (const window of [...this._windowSignals.keys()])
            this._unwatchWindow(window);
    }

    _changed() {
        if (this._debounceId)
            GLib.source_remove(this._debounceId);
        this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, CHANGE_DEBOUNCE_MS, () => {
            this._debounceId = 0;
            this._impl.emit_signal('WindowsChanged', null);
            return GLib.SOURCE_REMOVE;
        });
    }
}
