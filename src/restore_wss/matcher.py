"""Deciding whether a window that exists now is the window a snapshot remembers.

There is no stable window identity across a reboot — ``Meta.Window.get_id()`` is a per-session
handle, and the whole reason `Window State Manager` cannot do this job is that it keys on one
(``docs/similar-tools.md`` §3). So matching is a heuristic, and the best documented one available
is `smart-auto-move`'s: gate hard on ``wm_class``, then score the titles by the L1 distance between
their character histograms.

This is a port of that algorithm, with its author's constants, plus the two guards that make it
safe to act on:

* a **threshold** — below it, no match is claimed;
* a **minimum spread** — if the best and second-best candidates score too closely, the match is
  refused rather than guessed, because a wrong match moves the wrong window.

Pure functions over plain data on purpose: this is the piece whose thresholds have to be tunable
against fixtures, and it is the piece whose mistakes the user notices.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Window

# smart-auto-move's alphabet: digits, letters, punctuation and the space. Characters outside it
# (accents, CJK, box drawing) are ignored, which costs a little accuracy on non-ASCII titles and
# keeps the histogram small.
ALPHABET = (
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ "
)
_INDEX = {char: position for position, char in enumerate(ALPHABET)}

#: The largest L1 distance treated as "completely different". Two normalised histograms can differ
#: by at most 2.0, so this is the natural full-scale value.
TITLE_SIMILARITY_MAX_DIST = 2.0

#: Below this, a candidate is not a match at all.
DEFAULT_THRESHOLD = 0.8

#: The best candidate must beat the runner-up by this ratio, or the match is ambiguous. Two windows
#: of the same application with similar titles is the normal case — several terminals, several
#: editor windows — and getting it wrong is worse than not placing the window.
MIN_SCORE_SPREAD = 0.6

#: A title much shorter than the remembered one is usually the application's placeholder
#: ("Loading…", the bare app name) rather than the document, so it is penalised rather than
#: rewarded for happening to share letters.
MIN_TITLE_LEN_FOR_PENALTY = 8
TITLE_LEN_PENALTY_RATIO = 0.5
TITLE_LEN_PENALTY_FACTOR = 0.5


def title_histogram(title: str) -> list[float]:
    """Character frequencies, normalised by title length."""
    histogram = [0.0] * len(ALPHABET)
    if not title:
        return histogram
    for char in title:
        position = _INDEX.get(char)
        if position is not None:
            histogram[position] += 1.0
    length = float(len(title))
    return [count / length for count in histogram]


def title_similarity(left: str, right: str) -> float:
    """1.0 for identical titles, 0.0 for nothing in common."""
    if left == right:
        return 1.0
    left_histogram = title_histogram(left)
    right_histogram = title_histogram(right)
    distance = sum(abs(a - b) for a, b in zip(left_histogram, right_histogram, strict=True))
    return max(0.0, 1.0 - distance / TITLE_SIMILARITY_MAX_DIST)


def score(saved: Window, candidate: Window) -> float:
    """How much ``candidate`` looks like the window ``saved`` describes. 0.0 means "not it"."""
    # The hard gate. An application's window is never another application's window, and the app id
    # is the stronger of the two identifiers when both sides have one.
    if saved.app_id and candidate.app_id:
        if saved.app_id != candidate.app_id:
            return 0.0
    elif (saved.wm_class or candidate.wm_class) and saved.wm_class != candidate.wm_class:
        return 0.0

    value = title_similarity(saved.title or "", candidate.title or "")
    if value >= 1.0:
        return 1.0

    saved_title = saved.title or ""
    candidate_title = candidate.title or ""
    if (
        len(saved_title) > MIN_TITLE_LEN_FOR_PENALTY
        and len(candidate_title) < len(saved_title) * TITLE_LEN_PENALTY_RATIO
    ):
        value *= TITLE_LEN_PENALTY_FACTOR
    return value


@dataclass
class Match:
    """One saved window paired with the live window judged to be it."""

    saved: Window
    candidate: Window
    score: float
    #: Why this pairing was believed: ``exact-title``, ``title-similarity`` or ``only-candidate``.
    reason: str


@dataclass
class MatchResult:
    matches: list[Match]
    #: Saved windows with no live counterpart — these are what restore has to launch.
    unmatched_saved: list[Window]
    #: Live windows the snapshot does not describe — left alone, never closed.
    unmatched_live: list[Window]
    #: Pairings that scored well but were too close to a rival to act on. Reported, not applied.
    ambiguous: list[Match]


def match_windows(
    saved_windows: list[Window],
    live_windows: list[Window],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_spread: float = MIN_SCORE_SPREAD,
) -> MatchResult:
    """Pair saved windows with live ones, greedily, best score first.

    Greedy rather than optimal (Hungarian) on purpose: with the numbers involved — a few dozen
    windows — the difference is noise, and a greedy pass is explainable in a review dialog, which
    the plan requires it to be.
    """
    candidates: list[tuple[float, int, int]] = []
    for saved_index, saved in enumerate(saved_windows):
        for live_index, live in enumerate(live_windows):
            value = score(saved, live)
            if value > 0.0:
                candidates.append((value, saved_index, live_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    taken_saved: set[int] = set()
    taken_live: set[int] = set()
    matches: list[Match] = []
    ambiguous: list[Match] = []

    for value, saved_index, live_index in candidates:
        if saved_index in taken_saved or live_index in taken_live:
            continue
        if value < threshold:
            continue

        runner_up = max(
            (
                other_value
                for other_value, other_saved, other_live in candidates
                if other_saved == saved_index
                and other_live != live_index
                and other_live not in taken_live
            ),
            default=0.0,
        )
        pairing = Match(
            saved=saved_windows[saved_index],
            candidate=live_windows[live_index],
            score=value,
            reason="exact-title" if value >= 1.0 else "title-similarity",
        )
        # An exact title match is not ambiguous even if a rival scores as well: the rival would
        # have to have the identical title, in which case either choice is equally right.
        if value < 1.0 and runner_up > 0.0 and runner_up > value * min_spread:
            ambiguous.append(pairing)
            continue

        taken_saved.add(saved_index)
        taken_live.add(live_index)
        matches.append(pairing)

    return MatchResult(
        matches=matches,
        unmatched_saved=[w for i, w in enumerate(saved_windows) if i not in taken_saved],
        unmatched_live=[w for i, w in enumerate(live_windows) if i not in taken_live],
        ambiguous=ambiguous,
    )
