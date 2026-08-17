"""Correlating compositor windows with browser windows — including refusing to."""

from restore_wss.browser import BrowserWindow, Tab
from restore_wss.browsercorrelate import correlate, looks_private, score, strip_suffix
from restore_wss.model import Rect, Window


def win(title, **kwargs):
    return Window(wm_class="firefox", app_id="firefox_firefox.desktop", title=title, **kwargs)


def block(active_title, urls=("https://a/",), **kwargs):
    tabs = [Tab(url=url, title=f"page {i}") for i, url in enumerate(urls)]
    tabs.append(Tab(url="https://active/", title=active_title, active=True))
    return BrowserWindow(tabs=tabs, **kwargs)


def test_the_firefox_suffix_is_stripped_before_comparing():
    assert strip_suffix("GNOME — Mozilla Firefox") == "GNOME"
    assert strip_suffix("GNOME - Firefox") == "GNOME"
    assert strip_suffix("no suffix here") == "no suffix here"


def test_a_window_titled_after_its_active_tab_matches_outright():
    value, reason = score(win("GNOME — Mozilla Firefox"), block("GNOME"))
    assert value == 1.0
    assert "active tab" in reason


def test_two_windows_are_matched_to_their_own_tab_sets():
    windows = [win("Inbox — Mail — Mozilla Firefox"), win("Thesis — Mozilla Firefox")]
    blocks = [block("Thesis", urls=("https://docs/",)), block("Inbox — Mail")]
    result = correlate(windows, blocks)
    pairs = {c.window.title: c.block.active_title for c in result.matched}
    assert pairs == {
        "Inbox — Mail — Mozilla Firefox": "Inbox — Mail",
        "Thesis — Mozilla Firefox": "Thesis",
    }
    assert not result.unknown and not result.unclaimed


def test_an_indistinguishable_pair_holding_different_work_is_refused():
    """The failure mode that matters: two windows whose titles say nothing useful, and whose tab
    sets are not interchangeable. Attaching either is a coin toss with visible consequences."""
    windows = [win("Mozilla Firefox"), win("Mozilla Firefox")]
    blocks = [
        block("Mozilla Firefox", urls=("https://work/",)),
        block("Mozilla Firefox", urls=("https://holiday/",)),
    ]
    result = correlate(windows, blocks)
    assert result.matched == []
    assert len(result.unknown) == 2
    assert len(result.unclaimed) == 2


def test_an_indistinguishable_pair_showing_the_same_tabs_is_matched_anyway():
    """If the two candidates are interchangeable, refusing would be pedantic rather than careful."""
    windows = [win("Mozilla Firefox"), win("Mozilla Firefox")]
    blocks = [block("Mozilla Firefox"), block("Mozilla Firefox")]
    result = correlate(windows, blocks)
    assert len(result.matched) == 2


def test_a_single_browser_window_is_matched_by_being_the_only_one():
    result = correlate([win("something unrelated")], [block("nothing like it")])
    assert len(result.matched) == 1
    assert result.matched[0].confidence < 0.75
    assert "only browser window" in result.matched[0].block.reason


def test_geometry_breaks_a_tie_but_does_not_make_the_decision():
    windows = [win("Some page — Mozilla Firefox", frame=Rect(0, 0, 900, 700))]
    good = block("Some page")
    result = correlate(windows, [good], [{"width": 900, "height": 700, "maximized": False}])
    assert result.matched[0].confidence >= 0.75

    # A title that does not match is not rescued by perfect geometry.
    value, _ = score(
        win("Totally different — Mozilla Firefox", frame=Rect(0, 0, 900, 700)),
        block("aaaaaaaaaaaaaaaaaaaa"),
        {"width": 900, "height": 700},
    )
    assert value < 0.75


def test_a_browser_window_the_shell_does_not_show_is_reported_unclaimed():
    result = correlate([], [block("Some page")])
    assert len(result.unclaimed) == 1


def test_an_unmatched_window_keeps_its_browserness_and_loses_its_tabs():
    result = correlate([win("Mozilla Firefox")], [])
    assert result.matched == []
    assert result.unknown[0].block is None
    assert "confidently" in result.unknown[0].reason


def test_the_confidence_and_its_reason_are_written_onto_the_block():
    result = correlate([win("GNOME — Mozilla Firefox")], [block("GNOME")])
    stored = result.matched[0].block.to_json()
    assert stored.get("reason")
    assert result.matched[0].block.confidence == 1.0


def test_a_private_window_is_recognised_from_its_title_alone():
    assert looks_private(win("Example — Mozilla Firefox Private Browsing"))
    assert not looks_private(win("Example — Mozilla Firefox"))
