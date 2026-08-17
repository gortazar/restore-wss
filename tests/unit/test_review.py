"""What the review window shows, tested without a display."""

from restore_wss.review import build_model

PLAN = {
    "workspace_count": 3,
    "actions": [
        {
            "index": 0,
            "kind": "place",
            "description": "move  notes.txt — Text Editor → workspace 2, eDP-1",
            "reason": "exact-title 1.00",
            "confidence": 1.0,
        },
        {
            "index": 1,
            "kind": "launch",
            "description": "start writer for Thesis → workspace 1",
            "reason": "monitor DP-3 is not connected",
            "confidence": 0.6,
        },
    ],
    "skipped": [{"title": "Odd", "wm_class": "Odd", "reason": "no application id"}],
    "ambiguous": [{"title": "patxi@host: ~", "candidate": "patxi@host: ~/a", "score": 0.93}],
    "untouched": [{"title": "Inbox — Mail", "wm_class": "firefox"}],
    "vpn": [{"name": "work", "kind": "activate", "description": "vpn   reconnect work"}],
}


def test_the_heading_counts_windows_and_workspaces():
    assert build_model(PLAN).heading == "2 windows across 3 workspaces"


def test_confident_actions_are_ticked_and_doubtful_ones_are_not():
    rows = {row.index: row for row in build_model(PLAN).rows if row.kind == "window"}
    assert rows[0].selected
    assert not rows[1].selected


def test_the_reason_is_shown_next_to_the_item():
    rows = {row.index: row for row in build_model(PLAN).rows if row.kind == "window"}
    assert "not connected" in rows[1].subtitle
    assert rows[1].title.startswith("start writer for Thesis")


def test_only_ticked_window_rows_are_restored():
    model = build_model(PLAN)
    assert model.selected_indices == [0]
    model.rows[1].selected = True
    assert model.selected_indices == [0, 1]


def test_a_vpn_is_shown_but_is_not_a_per_item_choice():
    vpn_rows = [row for row in build_model(PLAN).rows if row.kind == "vpn"]
    assert len(vpn_rows) == 1
    assert vpn_rows[0].index == -1
    assert -1 not in build_model(PLAN).selected_indices


def test_what_is_not_being_done_is_spelled_out():
    notes = "\n".join(build_model(PLAN).notes)
    assert "Skipping Odd" in notes
    assert "too close to call" in notes
    assert "Leaving alone: Inbox — Mail" in notes


def test_an_empty_plan_says_so_rather_than_showing_an_empty_list():
    model = build_model({"actions": [], "workspace_count": 0})
    assert "Nothing to restore" in model.heading
    assert model.rows == []


BROWSER_PLAN = dict(
    PLAN,
    browser=[
        {
            "kind": "open",
            "description": "Reopen 7 tab(s): https://gnome.org/, https://a/ and 5 more",
        },
        {"kind": "skipped", "description": "A browser window: its tabs were never captured"},
        {"kind": "already", "description": "Thesis was already restored by Firefox"},
    ],
)


def test_only_actionable_browser_rows_get_a_switch():
    model = build_model(BROWSER_PLAN)
    rows = [row for row in model.rows if row.kind == "browser"]
    assert len(rows) == 1
    assert "7 tab(s)" in rows[0].title
    # Not a window action, so it never appears in the indices passed to Restore.
    assert -2 not in model.selected_indices


def test_browser_windows_with_nothing_to_do_are_notes_not_switches():
    """Offering a switch for a window whose tabs were never captured offers to do nothing."""
    notes = "\n".join(build_model(BROWSER_PLAN).notes)
    assert "never captured" in notes
    assert "already restored by Firefox" in notes


def test_the_browser_half_can_be_switched_off_as_a_whole():
    model = build_model(BROWSER_PLAN)
    assert model.restore_tabs
    for row in model.rows:
        if row.kind == "browser":
            row.selected = False
    assert not model.restore_tabs


def test_a_plan_with_no_browser_rows_leaves_tabs_alone():
    assert build_model(PLAN).restore_tabs is True
