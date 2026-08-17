"""Reconciling tabs with what Firefox restored by itself."""

from restore_wss.browser import BrowserWindow, Tab
from restore_wss.browserrestore import plan_browser_restore


def block(urls, active=None, pinned=(), confidence=1.0, titles=None):
    tabs = []
    for index, url in enumerate(urls):
        tabs.append(
            Tab(
                url=url,
                title=(titles or {}).get(url, f"page {index}"),
                pinned=url in pinned,
                active=url == active,
            )
        )
    return BrowserWindow(tabs=tabs, confidence=confidence)


def test_a_window_firefox_did_not_restore_is_asked_for():
    plan = plan_browser_restore(
        [("1", block(["https://a/", "https://b/"], active="https://b/"))], []
    )
    assert len(plan.actions) == 1
    action = plan.actions[0]
    assert action.urls == ["https://a/", "https://b/"]
    assert action.active == 1
    assert action.window_id == "1"


def test_a_window_firefox_already_restored_is_left_alone():
    """The point of reconciliation: the browser's own restore is on, so this must be a no-op."""
    saved = [("1", block(["https://a/", "https://b/"]))]
    live = [block(["https://a/", "https://b/"])]
    plan = plan_browser_restore(saved, live)
    assert plan.is_empty
    assert plan.already_open


def test_a_window_that_has_since_gained_tabs_still_counts_as_restored():
    """Closing what the user opened since would be worse than a missing window."""
    saved = [("1", block(["https://a/"]))]
    live = [block(["https://a/", "https://something-new/"])]
    plan = plan_browser_restore(saved, live)
    assert plan.is_empty
    assert plan.already_open


def test_tabs_are_never_appended_to_an_existing_window():
    """A saved window missing one tab is a whole window to create, not a tab to add."""
    saved = [("1", block(["https://a/", "https://b/"]))]
    live = [block(["https://a/"])]
    plan = plan_browser_restore(saved, live)
    assert len(plan.actions) == 1
    assert plan.actions[0].urls == ["https://a/", "https://b/"]


def test_pinned_state_and_order_are_carried():
    saved = [("1", block(["https://a/", "https://b/"], pinned={"https://a/"}))]
    plan = plan_browser_restore(saved, [])
    assert plan.actions[0].pinned == [True, False]
    assert plan.actions[0].urls == ["https://a/", "https://b/"]


def test_a_window_whose_tabs_were_never_captured_is_skipped_with_a_reason():
    plan = plan_browser_restore([("1", BrowserWindow(tabs=[], reason="tabs unknown"))], [])
    assert plan.actions == []
    assert "never captured" in plan.skipped[0][1]


def test_a_low_confidence_tab_set_is_shown_but_not_acted_on():
    saved = [("1", block(["https://a/"], confidence=0.3))]
    plan = plan_browser_restore(saved, [])
    assert plan.actions == []
    assert "sure which browser window" in plan.skipped[0][1]


def test_about_pages_are_not_worth_restoring():
    saved = [("1", block(["about:config", "about:newtab"]))]
    plan = plan_browser_restore(saved, [])
    assert plan.actions == []
    assert "no restorable URLs" in plan.skipped[0][1]


def test_two_identical_saved_windows_ask_for_two_windows():
    saved = [("1", block(["https://a/"])), ("2", block(["https://a/"]))]
    plan = plan_browser_restore(saved, [])
    assert len(plan.actions) == 2


def test_when_firefox_restored_one_of_two_identical_windows_the_other_is_still_asked_for():
    """One open window accounts for one saved window, not for every window that looks like it."""
    saved = [("1", block(["https://a/"])), ("2", block(["https://a/"]))]
    plan = plan_browser_restore(saved, [block(["https://a/"])])
    assert len(plan.already_open) == 1
    assert len(plan.actions) == 1


def test_running_it_twice_produces_nothing_the_second_time():
    saved = [("1", block(["https://a/", "https://b/"]))]
    first = plan_browser_restore(saved, [])
    assert first.actions
    # After the browser has done what the first plan asked, the same plan is empty.
    live = [block(action.urls) for action in first.actions]
    assert plan_browser_restore(saved, live).is_empty


def test_the_plan_explains_itself_in_words():
    saved = [
        ("1", block(["https://a/", "https://b/", "https://c/"])),
        ("2", BrowserWindow(tabs=[])),
    ]
    text = "\n".join(plan_browser_restore(saved, []).describe())
    assert "3 tab(s)" in text
    assert "and 1 more" in text
    assert "never captured" in text
