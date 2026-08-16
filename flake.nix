{
  description = "restore-wss — put the GNOME workspaces back the way they were (dev/test/build env)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # The daemon needs PyGObject at runtime (GLib main loop, Gio D-Bus, NetworkManager). The
        # tests deliberately do not: everything about the snapshot, matching and policy is pure
        # logic over fixtures, so `pytest tests/unit` runs anywhere. Only tests/dbus needs a bus.
        python = pkgs.python3.withPackages (ps: [
          ps.pygobject3
          ps.pytest
        ]);

        # Every source file the checks look at. Kept as one list so a new directory cannot be
        # silently left out of CI.
        src = pkgs.lib.cleanSource ./.;
      in {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.ruff
            pkgs.gjs # the extension half, and the syntax check
            pkgs.nodejs_22 # node --check on the extension sources
            pkgs.dbus # dbus-run-session for tests/dbus
            pkgs.glib # gdbus, glib-compile-schemas
            pkgs.jq
            pkgs.git
            pkgs.gnumake
          ];

          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            echo "restore-wss dev shell"
            echo "  make test          unit + dbus suites and lint"
            echo "  nix flake check    the same, hermetically — what CI runs"
            echo "  tools/nested-shell.sh start --extension src/extension --state /tmp/rwn"
          '';
        };

        checks = {
          # Pure logic: schema, matching, redaction, command policy, confidence scoring.
          unit = pkgs.runCommand "restore-wss-unit" { inherit src; nativeBuildInputs = [ python ]; } ''
            cp -r "$src" ./source && chmod -R u+w ./source && cd ./source
            PYTHONPATH=$PWD/src python -m pytest tests/unit -q | tee "$out"
          '';

          # The daemon's D-Bus surface, on a private bus.
          dbus = pkgs.runCommand "restore-wss-dbus"
            { inherit src; nativeBuildInputs = [ python pkgs.dbus pkgs.glib ]; } ''
            cp -r "$src" ./source && chmod -R u+w ./source && cd ./source
            export XDG_RUNTIME_DIR=$(mktemp -d)
            export HOME=$(mktemp -d)
            export PYTHONPATH=$PWD/src
            # The nix dbus looks for /etc/dbus-1/session.conf, which does not exist in the build
            # sandbox; the wrapper points it at the copy shipped next to the binary.
            tools/with-session-bus.sh python -m pytest tests/dbus -q | tee "$out"
          '';

          lint = pkgs.runCommand "restore-wss-lint"
            { inherit src; nativeBuildInputs = [ pkgs.ruff ]; } ''
            cp -r "$src" ./source && chmod -R u+w ./source && cd ./source
            ruff check src tests tools
            ruff format --check src tests tools
            echo "lint OK" > "$out"
          '';

          # The extension is GJS, which nothing in nixpkgs will type-check for us. A parse error
          # is still the most common way to ship a broken extension, and it is cheap to catch:
          # node parses the same ESM syntax, and the gi:// imports it cannot resolve do not matter
          # to --check.
          extension-syntax = pkgs.runCommand "restore-wss-extension-syntax"
            { inherit src; nativeBuildInputs = [ pkgs.nodejs_22 pkgs.jq ]; } ''
            cp -r "$src" ./source && chmod -R u+w ./source && cd ./source
            for f in $(find src/extension -name '*.js'); do
              cp "$f" "$f.mjs"
              node --check "$f.mjs" || { echo "syntax error in $f" >&2; exit 1; }
              rm "$f.mjs"
            done
            # metadata.json is what the Shell refuses to load an extension without.
            for field in uuid name description shell-version; do
              jq -e --arg f "$field" 'has($f)' src/extension/metadata.json >/dev/null \
                || { echo "metadata.json: missing \"$field\"" >&2; exit 1; }
            done
            echo "extension syntax OK" > "$out"
          '';
        };
      });
}
