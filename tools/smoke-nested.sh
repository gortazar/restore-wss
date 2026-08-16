#!/usr/bin/env bash
# End-to-end smoke test: a real headless GNOME Shell, the real extension, the real daemon.
#
#   tools/smoke-nested.sh
#
# Run it from a normal shell, NOT from inside `nix develop`: the nested Shell, its dconf service
# and gnome-terminal all have to be the system's, and a nix shell shadows dbus and gsettings with
# builds that cannot see the system's GSettings schemas — the symptom is the harness aborting with
# "no nested dconf database". Only the project's own Python runs here, and it needs nothing but
# python3-gi.
#
# This is the only test that exercises both processes against Mutter and a real application. It is
# not part of `nix flake check` (that would need a Shell in the build sandbox) and its result is
# recorded by hand in the wrapper's STATUS.md, as PLAN.md asks.
#
# What it proves, in order:
#   1. the extension loads and owns org.gnome.SessionCore
#   2. it reports the workspace and monitor layout, with a connector name from DisplayConfig
#   3. it reports a real window with its app id, pid and geometry
#   4. the daemon captures that window without being asked and writes it to disk
#   5. `restore-wss status` prints the live session
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
STATE="${RESTORE_WSS_SMOKE_STATE:-/tmp/restore-wss-smoke}"
FAILURES=0

step() { printf '\n== %s\n' "$1"; }
ok() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

cleanup() {
    [ -n "${DAEMON_PID:-}" ] && kill "$DAEMON_PID" 2>/dev/null || true
    tools/nested-shell.sh stop --state "$STATE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

step "booting a nested Shell with src/extension"
rm -rf "$STATE"
# env -u: sourcing a previous session's env would make the harness's dconf leak check compare
# against the wrong database and abort.
env -u XDG_CONFIG_HOME -u XDG_DATA_HOME -u XDG_CACHE_HOME -u DBUS_SESSION_BUS_ADDRESS \
    -u WAYLAND_DISPLAY tools/nested-shell.sh start --state "$STATE" --extension src/extension

# shellcheck source=/dev/null
source "$STATE/env"
export RESTORE_WSS_HOME="$STATE/home"
export PYTHONPATH="$PWD/src"
PYTHON="${PYTHON:-python3}"

call() {
    gdbus call --session --dest org.gnome.SessionCore \
        --object-path /org/gnome/SessionCore --method "org.gnome.SessionCore.$1" "${@:2}"
}

step "1. the extension owns org.gnome.SessionCore"
# The Shell owns its own bus name before it has finished enabling extensions, so "the Shell is up"
# is not "the core is up": wait for the name rather than assuming.
pong=""
for _ in $(seq 1 40); do
    pong="$(call Ping smoke 2>/dev/null || true)"
    [ -n "$pong" ] && break
    sleep 1
done
if echo "$pong" | grep -q "smoke"; then ok "Ping: $pong"; else fail "Ping (no reply in 40 s)"; fi

step "2. the layout carries a connector name"
layout="$(call GetLayout)"
if echo "$layout" | grep -q '"connector":"[^"]'; then
    ok "connector from DisplayConfig"
else
    fail "connector from DisplayConfig: $layout"
fi

step "3. a real window is reported"
(setsid gnome-terminal --working-directory="$HOME" -- bash -c 'sleep 600; true' >/dev/null 2>&1 &)
for _ in $(seq 1 40); do
    windows="$(call ListWindows)"
    echo "$windows" | grep -q 'gnome-terminal-server' && break
    sleep 1
done
if echo "$windows" | grep -q '"app_id":"org.gnome.Terminal.desktop"'; then
    ok "window with an app id"
else
    fail "window with an app id: $windows"
fi

step "4. the daemon captures it without being asked"
"$PYTHON" -m restore_wss daemon >"$STATE/daemon.log" 2>&1 &
DAEMON_PID=$!
snapshot="$STATE/home/state/session.json"
captured=false
for _ in $(seq 1 40); do
    if [ -f "$snapshot" ] && grep -q 'org.gnome.Terminal.desktop' "$snapshot"; then
        captured=true
        break
    fi
    sleep 1
done
if $captured; then ok "unprompted snapshot at $snapshot"; else fail "no unprompted snapshot"; fi

step "5. restore-wss status prints the live session"
if "$PYTHON" -m restore_wss status | tee /dev/stderr | grep -q "Live session from the daemon"; then
    ok "status"
else
    fail "status"
fi

printf '\n%s\n' "-----"
if [ "$FAILURES" -eq 0 ]; then
    echo "smoke: all checks passed"
else
    echo "smoke: $FAILURES check(s) failed"
fi
exit "$FAILURES"
