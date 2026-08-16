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
