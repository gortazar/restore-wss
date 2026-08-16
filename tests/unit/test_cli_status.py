import json

from restore_wss import cli
from restore_wss.model import Rect, Snapshot, Window


def _run(argv, source, capsys):
    code = cli.main(argv, source=source)
    return code, capsys.readouterr().out


def test_status_of_an_empty_session_says_so(capsys):
    source = cli.SnapshotSource(snapshot=Snapshot(), origin="daemon")
    code, out = _run(["status"], source, capsys)
    assert code == 0
    assert "no windows captured" in out


def test_status_lists_windows_by_workspace(capsys):
    snapshot = Snapshot(
        workspace_count=2,
        active_workspace=1,
        windows=[
            Window(
                wm_class="Soffice",
                title="Thesis — LibreOffice Writer",
                workspace=0,
                monitor="eDP-1",
                frame=Rect(0, 0, 900, 700),
            ),
            Window(wm_class="VSCodium", title="my-app — VSCodium", workspace=1, monitor="DP-1"),
        ],
    )
    code, out = _run(["status"], cli.SnapshotSource(snapshot, "daemon"), capsys)
    assert code == 0
    assert "Workspace 1" in out and "Workspace 2" in out  # 1-based for humans
    assert "Thesis — LibreOffice Writer" in out
    assert "my-app — VSCodium" in out
    assert "2 windows" in out


def test_status_json_is_the_snapshot_itself(capsys):
    snapshot = Snapshot(boot_id="abc", windows=[Window(wm_class="Foo")])
    code, out = _run(["status", "--json"], cli.SnapshotSource(snapshot, "daemon"), capsys)
    assert code == 0
    parsed = json.loads(out)
    assert parsed["boot_id"] == "abc"
    assert parsed["windows"][0]["wm_class"] == "Foo"


def test_status_says_where_the_answer_came_from(capsys):
    """A snapshot read off disk is not the same claim as one from a running daemon."""
    _, live = _run(["status"], cli.SnapshotSource(Snapshot(), "daemon"), capsys)
    assert "daemon" in live

    _, stale = _run(
        ["status"],
        cli.SnapshotSource(Snapshot(captured_at=1_700_000_000.0), "disk", path="/tmp/session.json"),
        capsys,
    )
    assert "/tmp/session.json" in stale
    assert "daemon is not running" in stale


def test_status_with_no_snapshot_at_all_is_not_an_error(capsys):
    code, out = _run(["status"], cli.SnapshotSource(None, "missing"), capsys)
    assert code == 0
    assert "nothing captured yet" in out


def test_version_is_printed(capsys):
    code = cli.main(["--version"])
    out = capsys.readouterr().out
    assert code == 0
    assert cli.VERSION in out
