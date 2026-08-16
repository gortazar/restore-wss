// Reading a Meta.Window into plain data.
//
// Everything here is a getter call and a property read: this runs inside gnome-shell, on the
// thread that draws the desktop, so there is no I/O, no spawning and nothing that can block.
// Interpretation — what is worth keeping, what a zero rect means, how a monitor index becomes a
// connector — happens in the daemon (src/restore_wss/capture.py), which is where it can be tested.

import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

/** Meta.WindowType, as the strings the protocol uses. */
function windowTypeName(window) {
    switch (window.get_window_type()) {
    case Meta.WindowType.NORMAL: return 'normal';
    case Meta.WindowType.DIALOG: return 'dialog';
    case Meta.WindowType.MODAL_DIALOG: return 'modal-dialog';
    case Meta.WindowType.UTILITY: return 'utility';
    case Meta.WindowType.DOCK: return 'dock';
    case Meta.WindowType.DESKTOP: return 'desktop';
    default: return 'other';
    }
}

/**
 * The application a window belongs to, as a desktop-file id.
 *
 * Shell.WindowTracker answers with a synthetic `window:N` id for a window it has not yet matched
 * to an installed application — which is the state every window is in at window-created time. It
 * is returned as-is rather than hidden, because the daemon skips those explicitly and counts them.
 */
function appIdOf(window) {
    const tracker = Shell.WindowTracker.get_default();
    const app = tracker.get_window_app(window);
    return app ? app.get_id() : '';
}

export function introspectWindow(window, stackingIndex = 0) {
    const frame = window.get_frame_rect();
    const workspace = window.get_workspace();
    const maximized = window.get_maximized();

    return {
        // Per-session only; the daemon records it for correlation within a run and never relies
        // on it across one (docs/platform-findings.md).
        id: `${window.get_id()}`,
        wm_class: window.get_wm_class() || '',
        title: window.get_title() || '',
        app_id: appIdOf(window),
        pid: window.get_pid() || 0,
        workspace: workspace ? workspace.index() : 0,
        monitor: window.get_monitor(),
        frame: { x: frame.x, y: frame.y, width: frame.width, height: frame.height },
        maximized: maximized === Meta.MaximizeFlags.BOTH,
        maximized_flags: maximized,
        fullscreen: window.is_fullscreen(),
        minimized: window.minimized,
        on_all_workspaces: window.is_on_all_workspaces(),
        skip_taskbar: window.is_skip_taskbar(),
        window_type: windowTypeName(window),
        // 0 = bottom of the stack. Restore uses it only to order what it raises.
        stacking: stackingIndex,
        gtk_app_id: window.get_gtk_application_id() || '',
        gtk_window_path: window.get_gtk_window_object_path() || '',
        sandboxed_app_id: window.get_sandboxed_app_id() || '',
        client_type: window.get_client_type() === Meta.WindowClientType.WAYLAND ? 'wayland' : 'x11',
    };
}

/** Every window, bottom of the stack first. */
export function introspectAllWindows(display) {
    const stack = display.sort_windows_by_stacking(
        display.get_tab_list(Meta.TabList.NORMAL_ALL, null));
    return stack.map((window, index) => introspectWindow(window, index));
}
