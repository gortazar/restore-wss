from restore_wss.documents import Context, Document, adapter_for, documents_for, is_document_path


def context(**kwargs):
    base = dict(cmdline=[], open_files=[], recent=[], app_history=[], captured_at=1000.0)
    base.update(kwargs)
    ctx = Context(**base)
    ctx.exists = lambda _path: True
    return ctx


def test_an_application_with_no_adapter_gets_no_document():
    """Tier 0 is a real answer: a guessed document is worse than none."""
    assert adapter_for("SomeRandomApp", "some.random.App.desktop") is None
    assert documents_for("SomeRandomApp", "some.random.App.desktop", "A title", context()) == []


def test_the_command_line_is_the_first_choice():
    docs = documents_for(
        "org.gnome.TextEditor",
        "org.gnome.TextEditor.desktop",
        "notes.txt — Text Editor",
        context(cmdline=["/usr/bin/gnome-text-editor", "/home/user/notes.txt"]),
    )
    assert docs[0].uri == "file:///home/user/notes.txt"
    assert docs[0].source == "cmdline"
    assert docs[0].confidence == 1.0


def test_flags_and_service_arguments_are_not_documents():
    docs = documents_for(
        "org.gnome.Nautilus",
        "org.gnome.Nautilus.desktop",
        "Documents",
        context(cmdline=["/usr/bin/nautilus", "--gapplication-service"]),
    )
    assert docs == []


def test_an_open_descriptor_is_used_when_the_command_line_has_nothing():
    docs = documents_for(
        "org.gnome.Nautilus",
        "org.gnome.Nautilus.desktop",
        "project",
        context(
            cmdline=["/usr/bin/nautilus", "--gapplication-service"],
            open_files=["/home/user/docs/project", "/dev/dri/card0", "/usr/lib/libc.so"],
        ),
    )
    assert [d.uri for d in docs] == ["file:///home/user/docs/project"]
    assert docs[0].confidence < 1.0


def test_plumbing_paths_are_never_documents():
    for path in (
        "/dev/null",
        "/proc/self/status",
        "/usr/share/x",
        "/nix/store/abc/lib.so",
        "/home/user/.cache/thumb.log",
    ):
        assert not is_document_path(path)
    assert is_document_path("/home/user/Thesis.odt")


def test_the_recent_store_is_correlated_by_application_and_time():
    now = 1_000_000.0
    recent = [
        ("file:///home/user/today.txt", "gnome-text-editor", now - 60),
        # A week old: the same application, but it says nothing about the window open now.
        ("file:///home/user/last-week.txt", "gnome-text-editor", now - 7 * 86400),
        ("file:///home/user/someone-elses.png", "Image Viewer", now - 60),
    ]
    docs = documents_for(
        "org.gnome.TextEditor",
        "org.gnome.TextEditor.desktop",
        "today.txt",
        context(recent=recent, captured_at=now),
    )
    assert [d.uri for d in docs] == ["file:///home/user/today.txt"]


def test_libreoffice_uses_its_own_history_because_it_is_not_in_the_recent_store():
    docs = documents_for(
        "Soffice",
        "libreoffice-writer.desktop",
        "Thesis — LibreOffice Writer",
        context(app_history=["file:///home/user/Thesis.odt"]),
    )
    assert docs[0].uri == "file:///home/user/Thesis.odt"
    assert docs[0].source == "app-history"


def test_the_title_is_a_last_resort_and_says_so():
    docs = documents_for(
        "Soffice", "libreoffice-writer.desktop", "Thesis — LibreOffice Writer", context()
    )
    assert docs[0].uri == "Thesis"
    assert docs[0].source == "title"
    assert docs[0].confidence <= 0.5


def test_a_document_that_no_longer_exists_is_not_offered():
    ctx = context(cmdline=["/usr/bin/gnome-text-editor", "/home/user/deleted.txt"])
    ctx.exists = lambda _path: False
    assert documents_for("org.gnome.TextEditor", "org.gnome.TextEditor.desktop", "", ctx) == []


def test_documents_round_trip_through_json():
    doc = Document(uri="file:///home/user/a.odt", source="cmdline", confidence=1.0)
    assert Document.from_json(doc.to_json()) == doc
    assert doc.path == "/home/user/a.odt"


def test_sources_are_tried_in_order_and_do_not_repeat_a_document():
    docs = documents_for(
        "org.gnome.TextEditor",
        "org.gnome.TextEditor.desktop",
        "notes.txt",
        context(
            cmdline=["/usr/bin/gnome-text-editor", "/home/user/notes.txt"],
            recent=[("file:///home/user/notes.txt", "gnome-text-editor", 1000.0)],
        ),
    )
    assert len(docs) == 1
    assert docs[0].source == "cmdline"
