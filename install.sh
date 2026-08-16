#!/usr/bin/env sh
# restore-wss installer.
#
#   curl -fsSL https://raw.githubusercontent.com/gortazar/restore-wss/main/install.sh | sh
#
# Downloads the latest release, verifies its checksum, and installs:
#   ~/.local/bin/restore-wss                                    the CLI and daemon
#   ~/.local/share/gnome-shell/extensions/session-core@…        the Shell extension
#   ~/.config/systemd/user/restore-wss.service                  the capture daemon unit
#   ~/.config/autostart/restore-wss-login.desktop               the login check
#
# Nothing is compiled and nothing needs root: it is a Python program and a GJS extension, and both
# go under $HOME. Set RESTORE_WSS_VERSION to install a specific tag.
set -eu

REPO="${RESTORE_WSS_REPO:-gortazar/restore-wss}"
VERSION="${RESTORE_WSS_VERSION:-latest}"
UUID="session-core@restore-wss.patxi"

BIN_DIR="${HOME}/.local/bin"
EXT_DIR="${HOME}/.local/share/gnome-shell/extensions/${UUID}"
LIB_DIR="${HOME}/.local/share/restore-wss"
UNIT_DIR="${HOME}/.config/systemd/user"
AUTOSTART_DIR="${HOME}/.config/autostart"

die() { echo "restore-wss: $*" >&2; exit 1; }
note() { echo "  $*"; }

command -v curl >/dev/null 2>&1 || die "curl is required"
command -v tar >/dev/null 2>&1 || die "tar is required"
python3 -c 'import gi' >/dev/null 2>&1 ||
    die "python3-gi is required (Debian/Ubuntu: sudo apt install python3-gi)"

if [ "$VERSION" = "latest" ]; then
    asset_url="https://github.com/${REPO}/releases/latest/download/restore-wss.tar.gz"
    sum_url="https://github.com/${REPO}/releases/latest/download/restore-wss.tar.gz.sha256"
else
    asset_url="https://github.com/${REPO}/releases/download/${VERSION}/restore-wss.tar.gz"
    sum_url="https://github.com/${REPO}/releases/download/${VERSION}/restore-wss.tar.gz.sha256"
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "restore-wss: downloading ${VERSION} …"
curl -fsSL "$asset_url" -o "$work/restore-wss.tar.gz" || die "could not download $asset_url"
if curl -fsSL "$sum_url" -o "$work/restore-wss.tar.gz.sha256" 2>/dev/null; then
    # The checksum is published beside the asset; a release without one is refused rather than
    # installed on trust.
    ( cd "$work" && sha256sum -c restore-wss.tar.gz.sha256 >/dev/null ) ||
        die "checksum mismatch — refusing to install"
    note "checksum verified"
else
    die "no published checksum for this release — refusing to install"
fi

tar -xzf "$work/restore-wss.tar.gz" -C "$work"
payload="$work/restore-wss"
[ -d "$payload" ] || die "the release archive does not look like a restore-wss release"

mkdir -p "$BIN_DIR" "$EXT_DIR" "$LIB_DIR" "$UNIT_DIR" "$AUTOSTART_DIR"

rm -rf "$LIB_DIR/restore_wss"
cp -r "$payload/restore_wss" "$LIB_DIR/"
cp -r "$payload/extension/." "$EXT_DIR/"
cp "$payload/data/systemd/restore-wss.service" "$UNIT_DIR/"
cp "$payload/data/autostart/restore-wss-login.desktop" "$AUTOSTART_DIR/"

cat > "$BIN_DIR/restore-wss" <<EOF
#!/usr/bin/env sh
# Installed by install.sh — the package lives beside it rather than on the system python path.
PYTHONPATH="${LIB_DIR}\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m restore_wss "\$@"
EOF
chmod +x "$BIN_DIR/restore-wss"

note "CLI       $BIN_DIR/restore-wss"
note "extension $EXT_DIR"
note "unit      $UNIT_DIR/restore-wss.service"

systemctl --user daemon-reload >/dev/null 2>&1 || true
systemctl --user enable --now restore-wss.service >/dev/null 2>&1 &&
    note "daemon    started" ||
    note "daemon    could not be started automatically; run: systemctl --user enable --now restore-wss.service"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "restore-wss: add $BIN_DIR to your PATH to use the 'restore-wss' command" ;;
esac

cat <<EOF

restore-wss ${VERSION} installed.

One more step, and it needs a log out: under Wayland the Shell cannot load an extension in place.

  1. log out and back in
  2. gnome-extensions enable ${UUID}
  3. restore-wss status

Nothing is captured until the extension is enabled — the daemon will say so.
EOF
