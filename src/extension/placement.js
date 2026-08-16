// Putting windows where the daemon says they were, and starting the applications that are
// missing. The only part of restore that has to be inside the compositor.
//
// Two facts from docs/platform-findings.md shape all of it:
//
//   * move_resize_frame() IS honoured for Wayland clients from an extension, including on a
//     workspace nobody is looking at — but the application's own minimum size wins, and when a
//     size is refused the accompanying move is dropped too. So a placement is a request, and the
//     verdict has to be read back later rather than assumed.
//   * The activation token issued at launch reaches the application's environment but is NOT
//     visible on the resulting window (get_startup_id() is null). Matching a new window to the
//     launch that asked for it is therefore "a window of the right application appeared while we
//     were waiting for one" — a guess, and it is labelled as one in the report.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

// A cold application start was measured at 24–30 s in a headless session, so a short timeout
// reports failure for launches that were merely slow.
const LAUNCH_TIMEOUT_MS = 90000;

/** Find a managed window by the id ListWindows reported. */
export function findWindow(windowId) {
    for (const actor of global.get_window_actors()) {
        const window = actor.meta_window;
        if (`${window.get_id()}` === `${windowId}`)
            return window;
    }
    return null;
}

/**
 * Create workspaces until there are at least `count` of them.
 *
 * Under the dynamic-workspaces setting GNOME appends and removes workspaces itself, and it always
 * keeps one empty one at the end; asking for N there means "make sure index N-1 exists", which is
 * done by appending and letting Mutter tidy up afterwards.
 */
export function ensureWorkspaces(count) {
    const manager = global.workspace_manager;
    while (manager.get_n_workspaces() < count)
        manager.append_new_workspace(false, global.get_current_time());
    return manager.get_n_workspaces();
}

function monitorIndexFor(connector, monitors) {
    if (!connector)
        return -1;
    const index = monitors?.indexFor?.(connector);
    return index === undefined ? -1 : index;
}

/**
 * Apply a placement to a window.
 *
 * Returns what was asked for; whether it stuck is a separate question, answered by
 * placementVerdict() once the client has had a chance to acknowledge the configure.
 */
export function placeWindow(window, placement, monitors) {
    const requested = { window_id: `${window.get_id()}` };

    // Un-maximize first: a maximized window ignores move_resize_frame(), so restoring a
    // floating geometry onto a maximized window silently does nothing.
    if (window.get_maximized() && !placement.maximized)
        window.unmaximize(Meta.MaximizeFlags.BOTH);
    if (window.is_fullscreen() && !placement.fullscreen)
        window.unmake_fullscreen();
    if (window.minimized && !placement.minimized)
        window.unminimize();

    if (Number.isInteger(placement.workspace)) {
        const workspace = global.workspace_manager.get_workspace_by_index(placement.workspace);
        if (workspace) {
            window.change_workspace(workspace);
            requested.workspace = placement.workspace;
        }
    }

    const monitorIndex = monitorIndexFor(placement.monitor, monitors);
    if (monitorIndex >= 0 && monitorIndex !== window.get_monitor()) {
        window.move_to_monitor(monitorIndex);
        requested.monitor = placement.monitor;
    }

    if (placement.frame) {
        let { x, y } = placement.frame;
        const { width, height } = placement.frame;
        if (placement.frame_space === 'monitor') {
            // The daemon sent coordinates relative to the monitor's top-left, which is what
            // survives the screen being at a different offset this time.
            const index = monitorIndex >= 0 ? monitorIndex : window.get_monitor();
            const geometry = Main.layoutManager.monitors[index];
            if (geometry) {
                x += geometry.x;
                y += geometry.y;
            }
        }
        window.move_resize_frame(true, x, y, width, height);
        requested.frame = { x, y, width, height };
    }

    if (placement.maximized) {
        window.maximize(Meta.MaximizeFlags.BOTH);
        requested.maximized = true;
    }
    if (placement.fullscreen) {
        window.make_fullscreen();
        requested.fullscreen = true;
    }
    if (placement.minimized) {
        window.minimize();
        requested.minimized = true;
    }

    return requested;
}

/**
 * Did a placement take?
 *
 * Reading the frame immediately after move_resize_frame() returns the OLD rect: a Wayland
 * geometry change is a negotiation, and the frame only changes once the client has acknowledged
 * the configure and committed a buffer. So this is deliberately a separate call, made later.
 */
export function placementVerdict(window, requested) {
    if (!window)
        return { state: 'gone' };
    const frame = window.get_frame_rect();
    if (frame.width === 0 && frame.height === 0)
        return { state: 'pending' };
    const verdict = {
        state: 'applied',
        actual: { x: frame.x, y: frame.y, width: frame.width, height: frame.height },
        workspace: window.get_workspace()?.index() ?? -1,
        monitor: window.get_monitor(),
    };
    if (requested?.frame) {
        const sizeHonoured = frame.width === requested.frame.width &&
            frame.height === requested.frame.height;
        const moveHonoured = frame.x === requested.frame.x && frame.y === requested.frame.y;
        verdict.size_honoured = sizeHonoured;
        verdict.move_honoured = moveHonoured;
        // The application's own minimum size wins, and a refused size drops the move with it.
        if (!sizeHonoured)
            verdict.note = 'the application refused the size';
    }
    return verdict;
}

/**
 * Launch an application, and place the window it produces.
 *
 * `uris` are handed to the desktop file, which is how a document is reopened (tier 1, M4). The
 * launch context issues an activation token so the Shell does not treat the new window as
 * demanding attention, even though the token cannot be read back off the window.
 */
export class Launcher {
    constructor(monitors) {
        this._monitors = monitors;
        this._pending = new Map(); // launchId -> record
        this._nextId = 1;
        this._createdId = global.display.connect('window-created',
            (_display, window) => this._onWindowCreated(window));
    }

    destroy() {
        if (this._createdId) {
            global.display.disconnect(this._createdId);
            this._createdId = 0;
        }
        for (const record of this._pending.values()) {
            if (record.timeoutId)
                GLib.source_remove(record.timeoutId);
        }
        this._pending.clear();
    }

    launch(desktopId, uris, placement) {
        const launchId = `launch-${this._nextId++}`;
        const appSystem = Shell.AppSystem.get_default();
        const app = appSystem.lookup_app(desktopId);
        const appInfo = app ? app.get_app_info()
            : Gio.DesktopAppInfo.new(desktopId);
        if (!appInfo) {
            this._pending.set(launchId, {
                state: 'failed', error: `no desktop file for ${desktopId}`,
            });
            return launchId;
        }

        const record = {
            state: 'launching',
            desktopId,
            placement,
            startedAt: GLib.get_monotonic_time(),
            // Recorded so the report can say how the window was attributed, per the plan's
            // requirement that a guess is visible as a guess.
            strategy: '',
        };
        this._pending.set(launchId, record);

        try {
            const context = global.create_app_launch_context(global.get_current_time(), -1);
            if (uris?.length)
                appInfo.launch_uris(uris, context);
            else
                appInfo.launch([], context);
            record.state = 'waiting';
        } catch (error) {
            record.state = 'failed';
            record.error = `${error}`;
            return launchId;
        }

        record.timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LAUNCH_TIMEOUT_MS, () => {
            record.timeoutId = 0;
            if (record.state === 'waiting') {
                record.state = 'timeout';
                record.error = `no window appeared within ${LAUNCH_TIMEOUT_MS / 1000} s`;
            }
            return GLib.SOURCE_REMOVE;
        });

        return launchId;
    }

    report(launchId) {
        const record = this._pending.get(launchId);
        if (!record)
            return { state: 'unknown' };
        const out = {
            state: record.state,
            window_id: record.windowId ?? '',
            strategy: record.strategy,
        };
        if (record.error)
            out.error = record.error;
        if (record.windowId) {
            out.verdict = placementVerdict(findWindow(record.windowId), record.requested);
        }
        return out;
    }

    _onWindowCreated(window) {
        // At window-created the window has no wm_class and no app yet: the Shell has not matched
        // it to an application. So wait for it to be identified rather than guessing now.
        const attempt = () => this._tryMatch(window);
        const ids = [
            window.connect('notify::wm-class', attempt),
            window.connect('notify::title', attempt),
            window.connect('notify::gtk-application-id', attempt),
        ];
        // A backstop for applications that never notify anything after mapping.
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
            attempt();
            for (const id of ids) {
                try {
                    window.disconnect(id);
                } catch {
                    // The window may already be gone.
                }
            }
            return GLib.SOURCE_REMOVE;
        });
        attempt();
    }

    _tryMatch(window) {
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        const appId = app ? app.get_id() : '';
        if (!appId || appId.startsWith('window:'))
            return;

        let best = null;
        for (const record of this._pending.values()) {
            if (record.state !== 'waiting' || record.desktopId !== appId)
                continue;
            if (!best || record.startedAt < best.startedAt)
                best = record;
        }
        if (!best)
            return;

        best.state = 'placed';
        best.windowId = `${window.get_id()}`;
        // Named honestly: this is "a window of the right application turned up", not proof.
        best.strategy = 'app-id-and-timing';
        if (best.timeoutId) {
            GLib.source_remove(best.timeoutId);
            best.timeoutId = 0;
        }
        // Geometry is 0x0 until the client commits a buffer, and placing then is ignored, so wait
        // for the first real size before applying the placement.
        this._placeWhenReady(window, best);
    }

    _placeWhenReady(window, record, attempt = 0) {
        const frame = window.get_frame_rect();
        if (frame.width === 0 && frame.height === 0 && attempt < 40) {
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
                this._placeWhenReady(window, record, attempt + 1);
                return GLib.SOURCE_REMOVE;
            });
            return;
        }
        try {
            record.requested = placeWindow(window, record.placement, this._monitors);
        } catch (error) {
            record.state = 'failed';
            record.error = `${error}`;
        }
    }
}
