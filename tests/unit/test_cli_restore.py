import json

from restore_wss import cli

PLAN = {
    "workspace_count": 3,
    "active_workspace": 1,
    "actions": [
        {
            "index": 0,
            "kind": "place",
            "app_id": "org.gnome.TextEditor.desktop",
            "window_id": "77",
            "title": "notes.txt — Text Editor",
            "description": "move  notes.txt — Text Editor → workspace 2, eDP-1 (exact-title 1.00)",
            "confidence": 1.0,
        },
        {
            "index": 1,
            "kind": "launch",
            "app_id": "libreoffice-writer.desktop",
            "window_id": "",
            "title": "Thesis — LibreOffice Writer",
            "description": "start libreoffice-writer.desktop for Thesis → workspace 1",
            "confidence": 0.6,
        },
    ],
    "skipped": [{"title": "Odd window", "wm_class": "Odd", "reason": "no application id"}],
    "ambiguous": [
        {"title": "patxi@host: ~", "candidate": "patxi@host: ~/a", "window_id": "3", "score": 0.93}
    ],
    "untouched": [{"title": "Inbox — Mail", "wm_class": "firefox"}],
}


class FakeClient:
    def __init__(self, plan=None):
        self.plan = plan if plan is not None else PLAN
        self.restored = None

    def plan_restore(self):
        return self.plan

    def restore(self, only=None):
        self.restored = only or []
        return {
            "results": [
                {"state": "done", "detail": "", "description": "move  notes.txt", "kind": "place"},
                {
                    "state": "failed",
                    "detail": "no window in 90 s",
                    "description": "start writer",
                    "kind": "launch",
                },
            ]
        }


def run(argv, client, capsys, confirm=None):
    code = cli.main(argv, client_factory=lambda: client, confirm=confirm)
    return code, capsys.readouterr().out


def test_dry_run_shows_the_plan_and_changes_nothing(capsys):
    client = FakeClient()
    code, out = run(["restore", "--dry-run"], client, capsys)
    assert code == 0
    assert "Restoring 2 window(s) across 3 workspace(s)" in out
    assert "move  notes.txt" in out
    assert "start libreoffice-writer.desktop" in out
    assert "--dry-run: nothing was changed." in out
    assert client.restored is None


def test_low_confidence_actions_are_marked(capsys):
    _, out = run(["restore", "--dry-run"], FakeClient(), capsys)
    lines = {line.strip()[:1] for line in out.splitlines() if "[1]" in line}
    assert lines == {"?"}


def test_skipped_ambiguous_and_untouched_windows_are_all_reported(capsys):
    _, out = run(["restore", "--dry-run"], FakeClient(), capsys)
    assert "skip  Odd window: no application id" in out
    assert "too close to call" in out
    assert "leaving alone: Inbox — Mail" in out


def test_nothing_to_do_is_said_plainly(capsys):
    empty = dict(PLAN, actions=[], skipped=[], ambiguous=[], untouched=[])
    code, out = run(["restore"], FakeClient(empty), capsys)
    assert code == 0
    assert "Nothing to restore" in out


def test_confirmation_is_required_by_default(capsys):
    client = FakeClient()
    code, out = run(["restore"], client, capsys, confirm=lambda: "n")
    assert code == 0
    assert client.restored is None
    assert "Nothing was changed." in out


def test_yes_skips_the_question_and_reports_each_action(capsys):
    client = FakeClient()
    code, out = run(["restore", "--yes"], client, capsys)
    assert client.restored == []
    assert "done" in out and "failed" in out
    assert "no window in 90 s" in out
    # A restore in which something failed exits non-zero, so a script notices.
    assert code == 1


def test_json_output_is_the_plan(capsys):
    code, out = run(["restore", "--json"], FakeClient(), capsys)
    assert code == 0
    assert json.loads(out)["actions"][0]["window_id"] == "77"


def test_no_daemon_is_an_error_with_advice(capsys):
    class Dead:
        def plan_restore(self):
            raise RuntimeError("name has no owner")

    code, out = run(["restore"], Dead(), capsys)
    assert code == 1
    assert "Is restore-wss-daemon running?" in out
