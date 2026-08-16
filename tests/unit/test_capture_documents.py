"""Documents, at the point where a snapshot is built and where a restore is planned."""

import json

from restore_wss.capture import capture, enrich_documents
from restore_wss.model import Snapshot
from restore_wss.plan import LAUNCH, build_plan

LAYOUT = json.dumps({"workspace_count": 1, "monitors": []})

EDITOR = {
    "id": "1",
    "wm_class": "org.gnome.TextEditor",
    "app_id": "org.gnome.TextEditor.desktop",
    "title": "notes.txt — Text Editor",
    "pid": 4242,
    "workspace": 0,
}
WRITER = {
    "id": "2",
    "wm_class": "Soffice",
    "app_id": "libreoffice-writer.desktop",
    "title": "Thesis — LibreOffice Writer",
    "pid": 4243,
    "workspace": 1,
}
UNKNOWN = {
    "id": "3",
    "wm_class": "SomeApp",
    "app_id": "some.App.desktop",
    "title": "Untitled",
    "pid": 4244,
    "workspace": 0,
}


def enriched(*records, procs=None, recent=None, history=None, captured_at=1000.0):
    result = capture(json.dumps(list(records)), LAYOUT, captured_at=captured_at)
    enrich_documents(
        result,
        None,
        procs=procs or (lambda pid: ([], [])),
        recent=recent or (lambda: []),
        history=history or (lambda: []),
    )
    return result.snapshot


def test_the_document_on_the_command_line_is_recorded():
    snapshot = enriched(
        EDITOR, procs=lambda pid: (["/usr/bin/gnome-text-editor", "/etc/hostname"], [])
    )
    # /etc is plumbing, so nothing is recorded — the filter is doing its job.
    assert "documents" not in snapshot.windows[0].extra

    snapshot = enriched(EDITOR, procs=lambda pid: (["/usr/bin/gnome-text-editor", "/tmp"], []))
    assert snapshot.windows[0].extra["documents"][0]["uri"] == "file:///tmp"


def test_an_application_with_no_adapter_is_never_investigated():
    seen = []

    def procs(pid):
        seen.append(pid)
        return [], []

    snapshot = enriched(UNKNOWN, procs=procs)
    assert seen == []
    assert snapshot.windows[0].extra == {}


def test_libreoffices_own_history_is_read_only_once_and_only_when_needed():
    calls = []

    def history():
        calls.append(1)
        return ["file:///home/user/Thesis.odt"]

    snapshot = enriched(WRITER, WRITER, EDITOR, history=history)
    assert len(calls) == 1
    assert snapshot.windows[0].extra["documents"][0]["uri"] == "file:///home/user/Thesis.odt"


def test_the_document_becomes_a_uri_the_restore_hands_to_the_application():
    snapshot = enriched(WRITER, history=lambda: ["file:///home/user/Thesis.odt"])
    plan = build_plan(Snapshot(windows=snapshot.windows), [])
    action = plan.actions[0]
    assert action.kind == LAUNCH
    assert action.uris == ["file:///home/user/Thesis.odt"]
    assert "opening file:///home/user/Thesis.odt" in action.describe()


def test_a_document_known_only_by_name_lowers_the_confidence_and_says_why():
    snapshot = enriched(WRITER)  # no history, no cmdline: only the title is left
    plan = build_plan(Snapshot(windows=snapshot.windows), [])
    action = plan.actions[0]
    assert action.uris == []
    assert action.confidence <= 0.5
    assert "only known by name (Thesis)" in action.reason


def test_an_excluded_path_is_not_recorded_as_a_document():
    from restore_wss.config import Config

    result = capture(json.dumps([EDITOR]), LAYOUT, captured_at=1000.0)
    enrich_documents(
        result,
        Config(exclude_paths=("/tmp",)),
        procs=lambda pid: (["/usr/bin/gnome-text-editor", "/tmp"], []),
        recent=lambda: [],
        history=lambda: [],
    )
    assert "documents" not in result.snapshot.windows[0].extra
