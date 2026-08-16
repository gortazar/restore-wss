#!/usr/bin/env bash
# Boot a headless GNOME Shell on a private session bus, with a chosen set of extensions.
#
# Provenance: this harness comes from the sibling `gnome-tasks` project by the same author
# (GPL-2.0-or-later), where it was developed and proven on this machine. Every comment about a
# trap in here was paid for once already; it is copied rather than rewritten deliberately.
#
#   tools/nested-shell.sh start [--extension DIR]... [--state DIR] [--monitor WxH]
#   tools/nested-shell.sh stop  [--state DIR]
#   tools/nested-shell.sh env   [--state DIR]     # prints shell-eval'able env for the session
#
# This is the instrument behind docs/platform-findings.md and the smoke tests: a real compositor
# with real Wayland/XWayland clients, no physical display, and — crucially — a *private* session
# bus, so nothing here touches the developer's own desktop.
#
# The environment for talking to the nested session is written to $STATE/env:
#
#   source /tmp/restore-wss-nested/env
#   gnome-extensions list            # the nested Shell's extensions, not yours
#   gio open ~/some.txt              # opens inside the nested session
set -euo pipefail

STATE="${RESTORE_WSS_NESTED_STATE:-/tmp/restore-wss-nested}"
MONITOR="1280x800"
# Must match wayland-[0-9]*: snapd's apparmor profile only lets confined apps (firefox, codium,
# libreoffice on Ubuntu) open $XDG_RUNTIME_DIR/wayland-N. A display called "gt-nested-0" is
# invisible to every snap on the system, which looks like the app failing to start.
WAYLAND_NAME="wayland-9"
EXTENSIONS=()
CMD="${1:-}"
shift || true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extension) EXTENSIONS+=("$2"); shift 2 ;;
        --state) STATE="$2"; shift 2 ;;
        --monitor) MONITOR="$2"; shift 2 ;;
        --display) WAYLAND_NAME="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

BUS_PATH="$STATE/bus"
LOG="$STATE/shell.log"

die() { echo "$*" >&2; exit 1; }

start() {
    [[ -e "$BUS_PATH" ]] && die "already running (or stale state): $STATE — run 'stop' first"

    # Remembered before the environment is switched over, so the leak check below can look at
    # the developer's real dconf database.
    REAL_XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

    rm -rf "$STATE"
    mkdir -p "$STATE"/{data,config,cache,run}
    chmod 700 "$STATE/run"

    local ext_home="$STATE/data/gnome-shell/extensions"
    mkdir -p "$ext_home"
    local uuids=()
    for dir in "${EXTENSIONS[@]:-}"; do
        [[ -n "$dir" ]] || continue
        [[ -f "$dir/metadata.json" ]] || die "no metadata.json in $dir"
        local uuid
        uuid=$(sed -n 's/.*"uuid"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$dir/metadata.json")
        [[ -n "$uuid" ]] || die "could not read uuid from $dir/metadata.json"
        cp -r "$dir" "$ext_home/$uuid"
        uuids+=("$uuid")
    done

    local enabled="[]"
    if [[ ${#uuids[@]} -gt 0 ]]; then
        local joined=""
        for uuid in "${uuids[@]}"; do
            [[ -n "$joined" ]] && joined+=", "
            joined+="'$uuid'"
        done
        enabled="[$joined]"
    fi

    # gnome-terminal-server refuses to start under a non-UTF-8 locale ("Non UTF-8 locale
    # (ANSI_X3.4-1968) is not supported!"), and LC_ALL=C is common in automation environments.
    local locale="${LC_ALL:-${LANG:-}}"
    case "$locale" in
        *UTF-8 | *utf8) ;;
        *) locale="C.UTF-8" ;;
    esac

    cat >"$STATE/env" <<EOF
export DBUS_SESSION_BUS_ADDRESS=unix:path=$BUS_PATH
export XDG_DATA_HOME=$STATE/data
export XDG_CONFIG_HOME=$STATE/config
export XDG_CACHE_HOME=$STATE/cache
export WAYLAND_DISPLAY=$WAYLAND_NAME
unset DISPLAY
export LC_ALL=$locale
export RESTORE_WSS_NESTED_STATE=$STATE
EOF

    # shellcheck source=/dev/null
    source "$STATE/env"

    # The isolated XDG_* variables MUST be exported before the private bus starts. dconf writes
    # are performed by the dconf service, which the bus activates, and that service takes its
    # database path from *its own* environment — so a bus started with the developer's real
    # XDG_CONFIG_HOME will happily write nested-session settings into the developer's real
    # ~/.config/dconf/user. (Learned the hard way: it silently rewrote a live desktop's
    # enabled-extensions list.)
    #
    # Our own dbus-daemon rather than dbus-run-session, because the address has to be
    # predictable: other processes (the probe harness, the daemon, gsettings) join this bus later.
    dbus-daemon --session --address="unix:path=$BUS_PATH" --print-pid --fork \
        >"$STATE/dbus.pid" 2>"$STATE/dbus.err" ||
        die "failed to start dbus-daemon: $(cat "$STATE/dbus.err")"

    # D-Bus-activated services (gnome-terminal-server, the portals, dconf) inherit the *bus
    # daemon's* environment, not the caller's — the same trap as the dconf leak above. Push the
    # session's variables into the bus activation environment so an activated app lands in the
    # nested session with a usable locale and display.
    dbus-update-activation-environment --verbose \
        LANG LC_ALL WAYLAND_DISPLAY XDG_DATA_HOME XDG_CONFIG_HOME XDG_CACHE_HOME \
        XDG_RUNTIME_DIR XDG_SESSION_TYPE >>"$STATE/activation-env.log" 2>&1 ||
        echo "warning: could not set the bus activation environment" >&2

    local real_db="${REAL_XDG_CONFIG_HOME:-$HOME/.config}/dconf/user"
    local real_db_before="absent"
    [[ -f "$real_db" ]] && real_db_before=$(stat -c '%Y %s %i' "$real_db")

    gsettings set org.gnome.shell disable-extension-version-validation true
    gsettings set org.gnome.shell enabled-extensions "$enabled"
    gsettings set org.gnome.desktop.interface enable-animations false
    # Static workspaces make layout assertions in the smoke tests deterministic; the real
    # extension has to cope with the dynamic default, which is tested separately.
    gsettings set org.gnome.mutter dynamic-workspaces false
    gsettings set org.gnome.desktop.wm.preferences num-workspaces 4

    # Belt and braces for the trap above: prove the writes landed in the nested database and
    # not in the real one, and refuse to go further if they did not.
    local real_db_after="absent"
    [[ -f "$real_db" ]] && real_db_after=$(stat -c '%Y %s %i' "$real_db")
    if [[ "$real_db_before" != "$real_db_after" ]]; then
        stop
        die "ABORTED: nested-session settings leaked into the real dconf database ($real_db).
Its state changed from [$real_db_before] to [$real_db_after] while configuring the nested
session. Check 'gsettings get org.gnome.shell enabled-extensions' before logging out."
    fi
    if [[ ! -f "$STATE/config/dconf/user" ]]; then
        stop
        die "ABORTED: no nested dconf database at $STATE/config/dconf/user — the settings went
somewhere unexpected. Refusing to continue."
    fi

    # Ubuntu ships extensions in /usr/share that would otherwise load into the nested session
    # and add noise (ding, dock, appindicators).
    XDG_DATA_DIRS="/usr/share" \
    setsid gnome-shell --headless --virtual-monitor "$MONITOR" \
        --wayland-display "$WAYLAND_NAME" >"$LOG" 2>&1 &
    echo $! >"$STATE/shell.pid"

    # Wait for the Shell to own its name on the private bus.
    for _ in $(seq 1 100); do
        if gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
            --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
            record_x11_display
            echo "nested shell up: state=$STATE display=$WAYLAND_NAME log=$LOG"
            echo "  source $STATE/env"
            return 0
        fi
        sleep 0.3
    done

    echo "nested shell did not come up; last 30 log lines:" >&2
    tail -30 "$LOG" >&2
    stop
    exit 1
}

# The nested Shell starts its own Xwayland on whatever display number is free, and announces it
# in its log. Without this, X11 clients in the nested session have no DISPLAY and silently fail,
# which is not the same finding as "XWayland does not work".
record_x11_display() {
    local display
    display=$(sed -n 's/.*Using public X11 display \(:[0-9]*\).*/\1/p' "$LOG" | head -1)
    if [[ -n "$display" ]]; then
        sed -i "s|^unset DISPLAY$|export DISPLAY=$display|" "$STATE/env"
        # Mutter generates a fresh X authority file per Xwayland; the newest one belongs to the
        # session we just started.
        local xauth
        xauth=$(find "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" -maxdepth 1 \
            -name '.mutter-Xwaylandauth*' -newermt '-60 seconds' -printf '%T@ %p\n' 2>/dev/null |
            sort -rn | head -1 | cut -d' ' -f2-)
        [[ -n "$xauth" ]] && echo "export XAUTHORITY=$xauth" >>"$STATE/env"
        echo "  nested Xwayland on DISPLAY=$display${xauth:+ (XAUTHORITY=$xauth)}"
    else
        echo "  no Xwayland found in the log; X11 clients will not run in this session" >&2
    fi
}

stop() {
    [[ -d "$STATE" ]] || return 0
    for pidfile in "$STATE/shell.pid" "$STATE/dbus.pid"; do
        if [[ -f "$pidfile" ]]; then
            local pid
            pid=$(cat "$pidfile")
            if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                for _ in $(seq 1 20); do
                    kill -0 "$pid" 2>/dev/null || break
                    sleep 0.2
                done
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
    done
    # Anything the nested session started (apps, portals, gvfs) shares its bus; killing the bus
    # socket path is what stops start() thinking it is still running.
    rm -f "$BUS_PATH" "$STATE/shell.pid" "$STATE/dbus.pid"
    echo "nested shell stopped (state kept in $STATE)"
}

case "$CMD" in
    start) start ;;
    stop) stop ;;
    env) cat "$STATE/env" ;;
    *) sed -n '2,12p' "$0" >&2; exit 2 ;;
esac
