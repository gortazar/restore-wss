# Everything here runs inside `nix develop`. `nix flake check` runs the same blocking set
# hermetically against the committed tree, and that is what CI invokes.

UUID := session-core@restore-wss.patxi
EXT_DIR := $(HOME)/.local/share/gnome-shell/extensions/$(UUID)
PYTHON ?= python

.PHONY: test test-unit test-dbus lint format check install uninstall nested

test: lint test-unit test-dbus

test-unit:
	PYTHONPATH=src $(PYTHON) -m pytest tests/unit -q

test-dbus:
	PYTHONPATH=src tools/with-session-bus.sh $(PYTHON) -m pytest tests/dbus -q

lint:
	ruff check src tests tools
	ruff format --check src tests tools

format:
	ruff format src tests tools

check:
	nix flake check

# Installing the extension needs a log out and back in: under Wayland the Shell cannot be
# reloaded in place.
install:
	mkdir -p "$(EXT_DIR)"
	cp -r src/extension/. "$(EXT_DIR)/"
	@echo "installed $(UUID) — log out and back in, then:"
	@echo "  gnome-extensions enable $(UUID)"

uninstall:
	rm -rf "$(EXT_DIR)"

# A throwaway Shell to try the extension against, with no risk to the real session.
nested:
	tools/nested-shell.sh start --extension src/extension --state /tmp/restore-wss-nested
