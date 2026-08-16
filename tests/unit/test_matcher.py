from restore_wss.matcher import match_windows, score, title_similarity
from restore_wss.model import Window


def w(app="editor.desktop", wm="Editor", title="", **kwargs):
    return Window(app_id=app, wm_class=wm, title=title, **kwargs)


def test_identical_titles_score_one():
    assert title_similarity("Thesis — LibreOffice Writer", "Thesis — LibreOffice Writer") == 1.0


def test_unrelated_titles_score_low():
    assert title_similarity("aaaaaaaa", "ZZZZZZZZ") < 0.2


def test_a_slightly_changed_title_still_scores_high():
    """Titles drift: an editor adds a modification marker, a terminal changes directory."""
    assert title_similarity("notes.txt — Text Editor", "*notes.txt — Text Editor") > 0.9


def test_a_different_application_never_matches():
    saved = w(app="libreoffice-writer.desktop", wm="Soffice", title="Thesis")
    live = w(app="codium.desktop", wm="VSCodium", title="Thesis")
    assert score(saved, live) == 0.0


def test_wm_class_gates_when_there_is_no_app_id():
    saved = Window(wm_class="Soffice", title="Thesis")
    assert score(saved, Window(wm_class="VSCodium", title="Thesis")) == 0.0
    assert score(saved, Window(wm_class="Soffice", title="Thesis")) == 1.0


def test_a_much_shorter_title_is_penalised():
    """ "Loading…" is the app's placeholder, not the document that was open."""
    saved = w(title="Thesis chapter 4 — LibreOffice Writer")
    generic = w(title="Loading")
    specific = w(title="Thesis chapter 4 — LibreOffice Writer")
    assert score(saved, generic) < score(saved, specific)


def test_the_obvious_case_matches():
    saved = [w(title="my-app — VSCodium")]
    live = [w(title="my-app — VSCodium", id="9")]
    result = match_windows(saved, live)
    assert len(result.matches) == 1
    assert result.matches[0].candidate.id == "9"
    assert result.matches[0].reason == "exact-title"
    assert not result.unmatched_saved


def test_a_window_the_snapshot_does_not_know_is_left_alone():
    result = match_windows([], [w(title="Somebody else's window", id="3")])
    assert result.matches == []
    assert [x.id for x in result.unmatched_live] == ["3"]


def test_a_window_that_is_not_running_is_reported_for_launching():
    saved = w(title="Thesis — LibreOffice Writer")
    result = match_windows([saved], [])
    assert result.unmatched_saved == [saved]


def test_two_similar_windows_of_the_same_app_are_refused_not_guessed():
    """The failure mode that matters: several terminals, and no way to tell them apart."""
    saved = [w(wm="Gnome-terminal", app="org.gnome.Terminal.desktop", title="patxi@host: ~")]
    live = [
        w(wm="Gnome-terminal", app="org.gnome.Terminal.desktop", title="patxi@host: ~/a", id="1"),
        w(wm="Gnome-terminal", app="org.gnome.Terminal.desktop", title="patxi@host: ~/b", id="2"),
    ]
    result = match_windows(saved, live)
    assert result.matches == []
    assert result.ambiguous, "an ambiguous pairing should be reported rather than dropped silently"


def test_exact_titles_win_over_similar_ones():
    saved = [w(title="chapter-4.odt — Writer"), w(title="chapter-5.odt — Writer")]
    live = [
        w(title="chapter-5.odt — Writer", id="five"),
        w(title="chapter-4.odt — Writer", id="four"),
    ]
    result = match_windows(saved, live)
    by_saved = {m.saved.title: m.candidate.id for m in result.matches}
    assert by_saved == {
        "chapter-4.odt — Writer": "four",
        "chapter-5.odt — Writer": "five",
    }


def test_one_live_window_is_claimed_only_once():
    saved = [w(title="Thesis — Writer"), w(title="Thesis — Writer")]
    live = [w(title="Thesis — Writer", id="only")]
    result = match_windows(saved, live)
    assert len(result.matches) == 1
    assert len(result.unmatched_saved) == 1
