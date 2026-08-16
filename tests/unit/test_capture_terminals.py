"""Terminal enrichment and exclusions, at the point where a snapshot is built."""

import json
from pathlib import Path

from restore_wss.capture import apply_exclusions, capture, enrich_terminals
from restore_wss.config import Config, load_config
from restore_wss.procwalk import Process

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "proc"
LAYOUT = json.dumps({"workspace_count": 1, "monitors": []})


def terminal_tree(pid):
    raw = json.loads((FIXTURES / "gnome-terminal-two-tabs.json").read_text())
    return Process.from_json(raw["tree"][0])


def windows_json(*records):
    return json.dumps(list(records))


TERMINAL_WINDOW = {
    "id": "1",
    "wm_class": "gnome-terminal-server",
    "app_id": "org.gnome.Terminal.desktop",
    "title": "patxi@host: ~",
    "pid": 177551,
}
EDITOR_WINDOW = {
    "id": "2",
    "wm_class": "org.gnome.TextEditor",
    "app_id": "org.gnome.TextEditor.desktop",
    "title": "notes.txt",
    "pid": 4242,
}


def test_a_terminal_window_gains_its_tabs():
    result = capture(windows_json(TERMINAL_WINDOW), LAYOUT, captured_at=1.0)
    enrich_terminals(result, load_config(Path("/nonexistent")), reader=terminal_tree)
    block = result.snapshot.windows[0].extra["terminal"]
    assert len(block["tabs"]) == 2
    assert any(tab["cwd"].endswith("my-repo") for tab in block["tabs"])


def test_the_terminal_block_survives_a_snapshot_round_trip():
    from restore_wss.model import Snapshot

    result = capture(windows_json(TERMINAL_WINDOW), LAYOUT, captured_at=1.0)
    enrich_terminals(result, None, reader=terminal_tree)
    again = Snapshot.loads(result.snapshot.dumps())
    assert again.windows[0].extra["terminal"]["tabs"]


def test_the_process_tree_of_a_non_terminal_is_never_read():
    """Reading a tree means recording a command line, so it happens only for declared terminals."""
    read = []

    def spy(pid):
        read.append(pid)
        return terminal_tree(pid)

    result = capture(windows_json(EDITOR_WINDOW), LAYOUT, captured_at=1.0)
    enrich_terminals(result, None, reader=spy)
    assert read == []
    assert "terminal" not in result.snapshot.windows[0].extra


def test_an_excluded_application_never_reaches_the_snapshot():
    config = Config(exclude_apps=("org.gnome.TextEditor.desktop",))
    result = capture(windows_json(TERMINAL_WINDOW, EDITOR_WINDOW), LAYOUT, captured_at=1.0)
    apply_exclusions(result, config)
    assert [w.id for w in result.snapshot.windows] == ["1"]
    assert result.skipped["excluded by config"] == 1


def test_an_excluded_path_removes_the_directory_and_the_command():
    config = Config(exclude_paths=("/home/user/.cache",))
    result = capture(windows_json(TERMINAL_WINDOW), LAYOUT, captured_at=1.0)
    enrich_terminals(result, config, reader=terminal_tree)
    tabs = result.snapshot.windows[0].extra["terminal"]["tabs"]
    private = [tab for tab in tabs if tab["cwd"] == ""]
    assert private, "the tab under the excluded path should have lost its directory"
    assert all("command" not in tab for tab in private)


def test_a_terminal_whose_process_is_gone_is_still_captured():
    result = capture(windows_json(TERMINAL_WINDOW), LAYOUT, captured_at=1.0)
    enrich_terminals(result, None, reader=lambda pid: None)
    assert result.snapshot.windows[0].extra == {}
