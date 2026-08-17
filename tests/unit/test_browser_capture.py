"""Browser capture at the point a snapshot is built, and how `status` shows it."""

import json

from restore_wss import cli
from restore_wss.browser import BrowserWindow, Tab, browser_of
from restore_wss.capture import capture, enrich_browsers
from restore_wss.config import Config
from restore_wss.model import Snapshot

LAYOUT = json.dumps({"workspace_count": 1, "monitors": []})

FIREFOX = {
    "id": "1",
    "wm_class": "firefox",
    "app_id": "firefox_firefox.desktop",
    "title": "GNOME — Mozilla Firefox",
    "pid": 500,
    "workspace": 0,
}
PRIVATE = {
    "id": "2",
    "wm_class": "firefox",
    "app_id": "firefox_firefox.desktop",
    "title": "Secrets — Mozilla Firefox Private Browsing",
    "pid": 500,
    "workspace": 1,
}
EDITOR = {
    "id": "3",
    "wm_class": "org.gnome.TextEditor",
    "app_id": "org.gnome.TextEditor.desktop",
    "title": "notes.txt",
    "pid": 4242,
}


def gnome_block(**kwargs):
    return BrowserWindow(tabs=[Tab(url="https://gnome.org/", title="GNOME", active=True)], **kwargs)


def enriched(*records, reports=None, reader=None, config=None):
    result = capture(json.dumps(list(records)), LAYOUT, captured_at=1000.0)
    enrich_browsers(
        result,
        config,
        reader=reader or (lambda: ([], [], None)),
        reports=reports,
    )
    return result


def test_the_extension_report_is_preferred_over_the_session_file():
    calls = []

    def reader():
        calls.append("session file")
        return [gnome_block()], [{}], None

    result = enriched(FIREFOX, reports=lambda: ([gnome_block()], [{}]), reader=reader)
    block = browser_of(result.snapshot.windows[0])
    assert block.source == "extension" or block.reason
    assert calls == [], "the session file should not be read when the extension has reported"


def test_the_session_file_is_used_when_the_extension_has_not_reported():
    result = enriched(
        FIREFOX, reports=lambda: ([], []), reader=lambda: ([gnome_block()], [{}], None)
    )
    assert browser_of(result.snapshot.windows[0]).urls == ["https://gnome.org/"]


def test_a_browser_with_no_source_at_all_is_recorded_as_tabs_unknown():
    result = enriched(FIREFOX)
    block = browser_of(result.snapshot.windows[0])
    assert block is not None
    assert block.tabs == []
    assert "no browser reported" in block.reason


def test_a_private_window_is_never_given_tabs():
    result = enriched(PRIVATE, reports=lambda: ([gnome_block()], [{}]))
    block = browser_of(result.snapshot.windows[0])
    assert block.tabs == []
    assert "private" in block.reason
    assert result.skipped["private browsing window"] == 1


def test_a_non_browser_window_is_left_alone():
    result = enriched(EDITOR, reports=lambda: ([gnome_block()], [{}]))
    assert result.snapshot.windows[0].extra == {}


def test_excluded_urls_never_reach_the_snapshot():
    config = Config(exclude_urls=("https://bank.example/*",))
    block = BrowserWindow(
        tabs=[
            Tab(url="https://bank.example/accounts", title="Accounts"),
            Tab(url="https://gnome.org/", title="GNOME", active=True),
        ]
    )
    result = enriched(FIREFOX, reports=lambda: ([block], [{}]), config=config)
    stored = browser_of(result.snapshot.windows[0])
    assert stored.urls == ["https://gnome.org/"]
    assert "excluded by config" in stored.reason


def test_a_substring_exclusion_works_too():
    assert Config(exclude_urls=("bank.example",)).excludes_url("https://bank.example/x")
    assert not Config(exclude_urls=("bank.example",)).excludes_url("https://gnome.org/")


def test_status_shows_the_tab_count_and_a_preview(capsys):
    window_json = dict(FIREFOX, workspace=0)
    result = capture(json.dumps([window_json]), LAYOUT, captured_at=1000.0)
    enrich_browsers(
        result,
        None,
        reports=lambda: (
            [
                BrowserWindow(
                    tabs=[
                        Tab(url="https://a/", title="First tab"),
                        Tab(url="https://b/", title="Second tab"),
                        Tab(url="https://c/", title="Third tab"),
                        Tab(url="https://d/", title="Fourth tab", active=True),
                    ]
                )
            ],
            [{}],
        ),
    )
    snapshot = Snapshot(windows=result.snapshot.windows)
    cli.main(["status"], source=cli.SnapshotSource(snapshot, "daemon"))
    out = capsys.readouterr().out
    assert "4 tab(s)" in out
    assert "First tab" in out
    assert "1 more" in out


def test_status_says_when_tabs_are_unknown(capsys):
    result = enriched(FIREFOX)
    cli.main(
        ["status"], source=cli.SnapshotSource(Snapshot(windows=result.snapshot.windows), "daemon")
    )
    assert "tabs unknown" in capsys.readouterr().out
