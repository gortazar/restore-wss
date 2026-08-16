import os
import stat

from restore_wss.model import Snapshot, Window
from restore_wss.storage import SnapshotStore, default_state_dir


def _snapshot(title: str) -> Snapshot:
    return Snapshot(captured_at=1.0, windows=[Window(wm_class="Foo", title=title)])


def test_save_then_load_round_trips(tmp_path):
    store = SnapshotStore(tmp_path / "state")
    store.save(_snapshot("first"))
    loaded = store.load()
    assert loaded is not None
    assert loaded.windows[0].title == "first"


def test_state_directory_is_private(tmp_path):
    store = SnapshotStore(tmp_path / "state")
    store.save(_snapshot("first"))
    mode = stat.S_IMODE(os.stat(store.directory).st_mode)
    assert mode == 0o700
    assert stat.S_IMODE(os.stat(store.current_path).st_mode) == 0o600


def test_the_previous_generation_is_kept(tmp_path):
    store = SnapshotStore(tmp_path / "state")
    store.save(_snapshot("first"))
    store.save(_snapshot("second"))
    assert store.load().windows[0].title == "second"
    previous = SnapshotStore._load_one(store.previous_path)
    assert previous.windows[0].title == "first"


def test_a_torn_current_file_falls_back_to_the_previous_one(tmp_path):
    """The point of the whole module: half a file must not lose the session."""
    store = SnapshotStore(tmp_path / "state")
    store.save(_snapshot("first"))
    store.save(_snapshot("second"))

    text = store.current_path.read_text()
    store.current_path.write_text(text[: len(text) // 2])  # a write cut short by a power failure

    loaded = store.load()
    assert loaded is not None
    assert loaded.windows[0].title == "first"


def test_no_snapshot_at_all_loads_as_none(tmp_path):
    assert SnapshotStore(tmp_path / "state").load() is None


def test_no_temp_files_are_left_behind(tmp_path):
    store = SnapshotStore(tmp_path / "state")
    store.save(_snapshot("first"))
    store.save(_snapshot("second"))
    assert sorted(p.name for p in store.directory.iterdir()) == [
        "session.json",
        "session.prev.json",
    ]


def test_state_dir_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RESTORE_WSS_HOME", str(tmp_path / "elsewhere"))
    assert default_state_dir() == tmp_path / "elsewhere" / "state"
