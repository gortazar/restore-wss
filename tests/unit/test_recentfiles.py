"""The two recent-document stores, against fixtures cut from the real files on this machine."""

from pathlib import Path

from restore_wss.recentfiles import read_libreoffice_history, read_recent_files

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "recent"


def test_the_freedesktop_store_yields_uri_application_and_time():
    entries = read_recent_files(FIXTURES / "recently-used.xbel")
    assert [(uri, app) for uri, app, _ in entries] == [
        ("file:///home/user/notes.txt", "gnome-text-editor"),
        ("file:///home/user/photo.png", "Image Viewer"),
    ]
    # Newest first, and the timestamps parsed rather than left at zero.
    assert entries[0][2] > entries[1][2] > 0


def test_a_missing_or_broken_store_contributes_nothing_rather_than_failing(tmp_path):
    assert read_recent_files(tmp_path / "absent.xbel") == []
    broken = tmp_path / "broken.xbel"
    broken.write_text("<xbel><bookmark")
    assert read_recent_files(broken) == []


def test_libreoffices_picklist_is_read_from_its_own_config():
    uris = read_libreoffice_history([FIXTURES / "registrymodifications.xcu"])
    assert uris == ["file:///home/user/Thesis.odt", "file:///home/user/budget.ods"]


def test_a_config_without_a_picklist_contributes_nothing(tmp_path):
    empty = tmp_path / "registrymodifications.xcu"
    empty.write_text("<oor:items/>")
    assert read_libreoffice_history([empty]) == []
    assert read_libreoffice_history([tmp_path / "absent.xcu"]) == []
