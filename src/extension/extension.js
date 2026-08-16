// The extension entry point: start the compositor-side core, stop it again.
//
// Everything else this project does happens in the daemon. Keeping the Shell half to this — a
// service that observes and (from M3) places windows — is what allows the daemon to spawn
// processes, write files and talk to NetworkManager without any of that living inside
// gnome-shell.

import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

import { SessionCore } from './sessionCore.js';

export default class RestoreWssExtension extends Extension {
    enable() {
        this._core = new SessionCore(this.metadata['version-name'] ?? '0');
        this._core.enable();
    }

    disable() {
        // Under Wayland the Shell cannot be reloaded in place, so disable() runs on log out and
        // on screen lock (the extension declares the unlock-dialog session mode so capture keeps
        // working while locked, which is exactly when an unclean shutdown tends to happen).
        this._core?.disable();
        this._core = null;
    }
}
