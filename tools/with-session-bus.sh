#!/usr/bin/env bash
# Run a command on a private D-Bus session bus.
#
#   tools/with-session-bus.sh python -m pytest tests/dbus
#
# Why not plain `dbus-run-session`: the dbus from nixpkgs looks for /etc/dbus-1/session.conf, which
# does not exist on a non-NixOS host or inside the nix build sandbox, and fails with
# "Failed to open /etc/dbus-1/session.conf". Its own copy of session.conf sits next to the binary,
# so point at that.
set -euo pipefail

config="${DBUS_SESSION_CONF:-}"
if [ -z "$config" ]; then
    daemon="$(command -v dbus-daemon)" || {
        echo "dbus-daemon not on PATH — are you inside 'nix develop'?" >&2
        exit 1
    }
    prefix="$(dirname "$(dirname "$(readlink -f "$daemon")")")"
    for candidate in "$prefix/share/dbus-1/session.conf" /usr/share/dbus-1/session.conf \
        /etc/dbus-1/session.conf; do
        [ -f "$candidate" ] && config="$candidate" && break
    done
fi
[ -n "$config" ] || { echo "no dbus session.conf found" >&2; exit 1; }

exec dbus-run-session --config-file="$config" -- "$@"
