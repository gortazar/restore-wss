// Monitor identity.
//
// Mutter's monitor *indices* renumber whenever a display is plugged, unplugged or reordered, so a
// snapshot keyed on them is wrong the moment a projector is involved. The names that do not move
// are the connector ("DP-3") and the EDID triple (vendor, product, serial), and neither is
// reachable from Meta on GNOME 46: `global.display.get_monitor_connector()` does not exist there
// (docs/platform-findings.md). They come from org.gnome.Mutter.DisplayConfig instead.
//
// The lookup is cached and refreshed on monitors-changed, because it is a synchronous D-Bus call
// and this code runs on the compositor's thread.

import Gio from 'gi://Gio';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const DISPLAY_CONFIG_NAME = 'org.gnome.Mutter.DisplayConfig';
const DISPLAY_CONFIG_PATH = '/org/gnome/Mutter/DisplayConfig';

export class MonitorConnectors {
    constructor() {
        this._byIndex = new Map();
        this._changedId = Main.layoutManager.connect('monitors-changed', () => this.refresh());
        this.refresh();
    }

    destroy() {
        if (this._changedId) {
            Main.layoutManager.disconnect(this._changedId);
            this._changedId = 0;
        }
    }

    /** Connector name for a Meta monitor index, or '' when DisplayConfig would not say. */
    connectorFor(index) {
        return this._byIndex.get(index)?.connector ?? '';
    }

    /** The layout the daemon stores: one entry per monitor, in Meta index order. */
    describe() {
        return Main.layoutManager.monitors.map((monitor, index) => {
            const identity = this._byIndex.get(index) ?? {};
            return {
                index,
                connector: identity.connector ?? '',
                vendor: identity.vendor ?? '',
                product: identity.product ?? '',
                serial: identity.serial ?? '',
                geometry: {
                    x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height,
                },
                scale: monitor.geometry_scale ?? 1,
                primary: index === Main.layoutManager.primaryIndex,
            };
        });
    }

    /**
     * Refresh the cache. Asynchronous, and it has to be.
     *
     * org.gnome.Mutter.DisplayConfig is served by *mutter itself*, which is the process this code
     * runs in. A synchronous call therefore blocks the thread that would have dispatched the
     * reply, and the call times out — observed, with the extension's own error in the Shell log:
     *   "restore-wss: DisplayConfig.GetCurrentState failed: Gio.IOErrorEnum: Timeout was reached"
     * The consequence is that connector names are unknown for the first few milliseconds after
     * enable(); the daemon copes, because a window with no connector is recorded as such.
     */
    refresh() {
        Gio.DBus.session.call(
            DISPLAY_CONFIG_NAME, DISPLAY_CONFIG_PATH, DISPLAY_CONFIG_NAME,
            'GetCurrentState', null, null, Gio.DBusCallFlags.NONE, 2000, null,
            (connection, result) => {
                try {
                    this._ingest(connection.call_finish(result));
                } catch (error) {
                    logError(error, 'restore-wss: DisplayConfig.GetCurrentState failed');
                }
            });
    }

    _ingest(state) {
        this._byIndex.clear();

        // (u serial, monitors, logical monitors, properties). A logical monitor lists the
        // connectors it is made of, and the logical monitors are in the same order as Meta's
        // monitor indices, so the mapping index -> connector goes through them.
        const [, monitors, logicalMonitors] = state.deepUnpack();

        const identityByConnector = new Map();
        for (const monitor of monitors) {
            const [[connector, vendor, product, serial]] = monitor.deepUnpack
                ? monitor.deepUnpack() : monitor;
            identityByConnector.set(connector, { connector, vendor, product, serial });
        }

        logicalMonitors.forEach((logical, index) => {
            const unpacked = logical.deepUnpack ? logical.deepUnpack() : logical;
            const connectors = unpacked[5] ?? [];
            const first = connectors[0];
            const connector = Array.isArray(first) ? first[0] : first?.[0];
            if (connector && identityByConnector.has(connector))
                this._byIndex.set(index, identityByConnector.get(connector));
        });
    }
}

/** The workspace half of the layout. */
export function describeWorkspaces() {
    const manager = global.workspace_manager;
    const names = [];
    for (let i = 0; i < manager.get_n_workspaces(); i++) {
        // Returns "Workspace N" when the user has not named one; the daemon keeps the string as
        // it comes and treats names as best effort, per the answered open question in PLAN.md.
        names.push(Meta.prefs_get_workspace_name(i) ?? '');
    }
    return {
        workspace_count: manager.get_n_workspaces(),
        active_workspace: manager.get_active_workspace_index(),
        workspace_names: names,
        // Restore has to create workspaces, and under the dynamic setting GNOME manages their
        // number itself — so the daemon needs to know which regime it is in.
        dynamic_workspaces: Meta.prefs_get_dynamic_workspaces(),
    };
}
