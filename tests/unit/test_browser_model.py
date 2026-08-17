"""The browser block: round trips, and what a v0.1 snapshot must still do."""

import json

from restore_wss.browser import (
    FROM_EXTENSION,
    FROM_SESSION_FILE,
    BrowserWindow,
    Tab,
    attach,
    browser_of,
    is_browser,
)
from restore_wss.model import Snapshot, Window


def test_a_browser_block_round_trips():
    block = BrowserWindow(
        family="firefox",
        version="142.0",
        profile="cqdb58zj.default",
        window_id="7",
        source=FROM_EXTENSION,
        tabs=[
            Tab(url="https://example.com/", title="Example", pinned=True),
            Tab(url="https://gnome.org/", title="GNOME", active=True, group="work"),
        ],
    )
    assert BrowserWindow.from_json(block.to_json()) == block


def test_the_block_survives_a_whole_snapshot_round_trip():
    window = Window(wm_class="firefox", app_id="firefox_firefox.desktop")
    attach(window, BrowserWindow(tabs=[Tab(url="https://example.com/", title="Example")]))
    snapshot = Snapshot(windows=[window])
    again = Snapshot.loads(snapshot.dumps())
    block = browser_of(again.windows[0])
    assert block is not None
    assert block.urls == ["https://example.com/"]


def test_a_v0_1_snapshot_reads_and_restores_exactly_as_before():
    """The migration requirement: no browser block means nothing changes."""
    v01 = json.dumps(
        {
            "schema": 1,
            "captured_at": 1.0,
            "windows": [
                {
                    "id": "1",
                    "wm_class": "firefox",
                    "app_id": "firefox_firefox.desktop",
                    "title": "Example — Mozilla Firefox",
                    "workspace": 2,
                }
            ],
        }
    )
    snapshot = Snapshot.loads(v01)
    assert browser_of(snapshot.windows[0]) is None
    # And it still round trips without gaining anything.
    assert "browser" not in snapshot.dumps()


def test_defaults_are_honest_about_what_is_unknown():
    block = BrowserWindow.from_json({"tabs": [{"url": "https://a/"}]})
    assert block.source == FROM_EXTENSION
    assert block.profile == ""
    assert block.confidence == 1.0
    assert block.tabs[0].title == ""


def test_the_source_is_recorded_because_the_two_claims_differ():
    from_file = BrowserWindow(source=FROM_SESSION_FILE, tabs=[Tab(url="https://a/")])
    assert from_file.to_json()["source"] == "session-file"


def test_the_active_tabs_title_is_what_correlation_uses():
    block = BrowserWindow(
        tabs=[
            Tab(url="https://a/", title="First"),
            Tab(url="https://b/", title="Second", active=True),
        ]
    )
    assert block.active_title == "Second"
    # With no active flag, the first tab is the best available guess.
    assert BrowserWindow(tabs=[Tab(url="https://a/", title="First")]).active_title == "First"


def test_a_low_confidence_block_says_so_in_the_file():
    block = BrowserWindow(confidence=0.4, reason="two windows with similar titles")
    written = block.to_json()
    assert written["confidence"] == 0.4
    assert written["reason"] == "two windows with similar titles"


def test_only_declared_browsers_are_treated_as_browsers():
    assert is_browser("firefox")
    assert not is_browser("org.gnome.TextEditor")
