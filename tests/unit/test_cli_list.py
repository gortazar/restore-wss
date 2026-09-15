"""`restore-wss list`: what it prints, and what it returns.

The command shipped in v0.1 with no tests at all, which is how it kept a function that declared an
``int`` and only ever produced ``0`` (`python:S3516`). These cover both halves of that: the output
a user sees, and the contract the function actually has.
"""

import json

from restore_wss import cli
from restore_wss.model import Snapshot, Window
from restore_wss.storage import SnapshotStore


def a_snapshot(title: str, captured_at: float = 1_700_000_000.0) -> Snapshot:
    return Snapshot(
        captured_at=captured_at,
        boot_id="boot-1",
        windows=[Window(wm_class="Foo", app_id="foo.desktop", title=title)],
    )


def state_dir(monkeypatch, tmp_path):
    """Point the CLI at a state directory of our own, the way a user's would be laid out."""
    monkeypatch.setenv("RESTORE_WSS_HOME", str(tmp_path))
    return SnapshotStore(tmp_path / "state")


def test_no_snapshots_yet_says_so_and_succeeds(monkeypatch, tmp_path, capsys):
    state_dir(monkeypatch, tmp_path)
    assert cli.main(["list"]) == 0
    assert "no snapshots yet" in capsys.readouterr().out


def test_both_generations_are_listed_newest_first(monkeypatch, tmp_path, capsys):
    store = state_dir(monkeypatch, tmp_path)
    store.save(a_snapshot("older"))
    store.save(a_snapshot("newer"))

    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert " current" in out
    assert "previous" in out
    # The point of the command: there are only ever two, and it says so rather than implying a
    # history the user could pick from.
    assert "Only these two are kept" in out
    assert out.index("current") < out.index("previous")


def test_each_line_carries_the_window_count_and_the_path(monkeypatch, tmp_path, capsys):
    store = state_dir(monkeypatch, tmp_path)
    store.save(a_snapshot("only one"))

    cli.main(["list"])
    out = capsys.readouterr().out
    assert "1 window(s)" in out
    assert str(store.current_path) in out


def test_json_output_is_machine_readable(monkeypatch, tmp_path, capsys):
    store = state_dir(monkeypatch, tmp_path)
    store.save(a_snapshot("first"))

    assert cli.main(["list", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)
    assert [entry["generation"] for entry in entries] == ["current"]
    assert entries[0]["windows"] == 1
    assert entries[0]["boot_id"] == "boot-1"


def test_json_output_of_an_empty_state_directory_is_an_empty_list(monkeypatch, tmp_path, capsys):
    state_dir(monkeypatch, tmp_path)
    assert cli.main(["list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_listing_does_not_compute_an_exit_code(monkeypatch, tmp_path, capsys):
    """The regression guard for `python:S3516`.

    `_list` prints; it cannot fail and has nothing to report, so it returns nothing and `main`
    supplies the exit code — the shape `status` two arms above already uses. If someone gives it an
    `int` return again, the invariant-return finding comes back with it, so this asserts the
    contract rather than the value.
    """
    store = state_dir(monkeypatch, tmp_path)
    store.save(a_snapshot("first"))

    args = cli._build_parser().parse_args(["list"])
    assert cli._list(args) is None
    capsys.readouterr()
