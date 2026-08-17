"""Reading Firefox's session store, against a fixture in the shape of the real file."""

import json
import struct
from pathlib import Path

from tests.unit.test_mozlz4 import literal_block  # noqa: E402  (shared LZ4 helper)

from restore_wss.browser import FROM_SESSION_FILE
from restore_wss.mozlz4 import MAGIC
from restore_wss.sessionfile import find_session_files, read_windows, windows_from_document

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "browser"


def document():
    return json.loads((FIXTURES / "session-two-windows.json").read_text())


def test_the_windows_and_their_tabs_are_read():
    blocks, geometries = windows_from_document(document(), profile="abc.default", age=30.0)
    assert len(blocks) == 2  # the third window is only an about:blank
    assert [len(block.tabs) for block in blocks] == [2, 1]
    assert blocks[0].source == FROM_SESSION_FILE
    assert blocks[0].profile == "abc.default"
    assert "30s old" in blocks[0].reason
    assert len(geometries) == 2


def test_the_current_page_of_a_tab_is_used_not_its_history():
    """A tab's earlier entries are its back history, which is out of scope."""
    blocks, _ = windows_from_document(document())
    urls = blocks[0].urls
    assert "https://gnome.org/" in urls
    assert "https://old.example.com/" not in urls


def test_both_entry_shapes_seen_in_the_wild_are_handled():
    blocks, _ = windows_from_document(document())
    titles = {tab.url: tab.title for tab in blocks[0].tabs}
    assert titles["https://mail.example.com/"] == "Inbox — Mail"  # minimal entry
    assert titles["https://gnome.org/"] == "GNOME"  # full session-history entry


def test_pinned_and_selected_state_survive():
    blocks, _ = windows_from_document(document())
    pinned = [tab for tab in blocks[0].tabs if tab.pinned]
    assert [tab.url for tab in pinned] == ["https://mail.example.com/"]
    assert blocks[0].active_title == "GNOME"  # window 1 has selected: 2


def test_blank_and_hidden_tabs_are_not_tabs_anybody_opened():
    blocks, _ = windows_from_document(document())
    urls = [url for block in blocks for url in block.urls]
    assert "about:newtab" not in urls
    assert "about:blank" not in urls
    assert not any("hidden" in url for url in urls)


def test_the_geometry_is_carried_through_for_correlation():
    _, geometries = windows_from_document(document())
    assert geometries[0]["maximized"] is True
    assert (geometries[1]["x"], geometries[1]["width"]) == (1920, 900)
    assert geometries[1]["maximized"] is False


def test_a_real_container_is_read_end_to_end(tmp_path):
    profile = tmp_path / "firefox" / "xyz.default" / "sessionstore-backups"
    profile.mkdir(parents=True)
    payload = json.dumps(document()).encode()
    (profile / "recovery.jsonlz4").write_bytes(
        MAGIC + struct.pack("<I", len(payload)) + literal_block(payload)
    )
    blocks, geometries, source = read_windows(roots=[str(tmp_path / "firefox")])
    assert source is not None
    assert source.profile == "xyz.default"
    assert len(blocks) == 2
    assert len(geometries) == 2


def test_no_firefox_at_all_is_not_an_error(tmp_path):
    assert find_session_files(roots=[str(tmp_path / "nothing")]) == []
    assert read_windows(roots=[str(tmp_path / "nothing")]) == ([], [], None)


def test_a_file_that_will_not_decode_contributes_nothing(tmp_path):
    profile = tmp_path / "firefox" / "broken.default" / "sessionstore-backups"
    profile.mkdir(parents=True)
    (profile / "recovery.jsonlz4").write_bytes(b"not mozlz4 at all")
    assert read_windows(roots=[str(tmp_path / "firefox")]) == ([], [], None)
