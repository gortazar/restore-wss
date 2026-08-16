#!/usr/bin/env bash
# Dump the Wayland globals the running compositor advertises.
#
# This is how docs/similar-tools.md establishes whether xdg_session_management_v1 (formerly
# xx_session_management_v1) is available here: there is no way to ask Mutter over D-Bus, and the
# symbol is not in libmutter either, because protocol interfaces are generated statics. So connect
# a real Wayland client and read the registry.
#
# GTK4 is used as the client because it is present wherever GNOME is, and WAYLAND_DEBUG=1 makes
# libwayland log every wl_registry.global event before the client has done anything else.
#
# Usage: tools/wayland-globals.sh [> docs/probe-data/wayland-globals.txt]
set -euo pipefail

if [ -z "${WAYLAND_DISPLAY:-}" ]; then
    echo "no WAYLAND_DISPLAY — run this from inside a Wayland session" >&2
    exit 1
fi

probe="$(mktemp --suffix=.js)"
trap 'rm -f "$probe"' EXIT
cat >"$probe" <<'EOF'
import Gtk from 'gi://Gtk?version=4.0';
Gtk.init();
EOF

# gjs exits as soon as init() returns; the registry has been dumped by then.
WAYLAND_DEBUG=1 timeout 30 gjs -m "$probe" 2>&1 |
    grep -oE 'global\([0-9]+, "[a-z_0-9]+"' |
    grep -oE '"[a-z_0-9]+"' |
    tr -d '"' |
    sort -u
