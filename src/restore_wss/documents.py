"""Which document a window is showing.

There is no general answer to this on Linux, so ``PLAN.md`` asks for an honest ladder instead of
one mechanism pretending to cover everything. This module is that ladder, and every rung carries
a **confidence**, because the review step needs to know which guesses to put in front of the user.

* **Tier 0 — the application only.** No document. Restore launches the app with no arguments. This
  is the answer for anything with no adapter, and it is a fine answer.
* **Tier 1 — recovered by introspection.** In order of preference:

  1. the command line (``/proc/<pid>/cmdline``) — exact when the app was launched with its
     document, useless for D-Bus-activated apps, which show ``--gapplication-service`` instead;
  2. open file descriptors (``/proc/<pid>/fd``) — Nautilus holds one on the directory it shows;
  3. the application's own history — LibreOffice keeps a picklist in its config rather than in the
     freedesktop store, which is why the headline "Thesis in LibreOffice" case needs an adapter;
  4. the freedesktop recent store (``recently-used.xbel``), correlated by application and time;
  5. per-app title parsing, declared for that application and never applied globally.

* **Tier 2 — the application reports its own state.** Not built here. The schema and the D-Bus API
  leave room for it (a window may carry ``documents`` written by something else).

Adapters are data, not code, wherever possible: which sources an application supports, and any
title pattern, are a table. Adding an application is a small, testable change — see
``docs/app-adapters.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

#: Confidence by source, used to decide what the review step pre-ticks. These are not probabilities
#: — they are an ordering with gaps wide enough to be meaningful.
CONFIDENCE = {
    "cmdline": 1.0,  # the document is literally in the process's argv
    "app-history": 0.8,  # the application's own most-recent-document list
    "fd": 0.7,  # an open descriptor, which may be incidental
    "recent-files": 0.6,  # the right app opened this recently — probably this window
    "title": 0.5,  # a declared per-app pattern over a string meant for humans
}

#: Paths that are never documents, whatever they look like.
_IGNORED_PREFIXES = (
    "/dev/",
    "/proc/",
    "/sys/",
    "/run/",
    "/usr/",
    "/nix/store/",
    "/snap/",
    "/var/lib/",
    "/etc/",
)
_IGNORED_SUFFIXES = (".so", ".desktop", ".log", ".lock", ".pid", ".cache")


@dataclass
class Document:
    """One document a window is showing."""

    uri: str
    source: str
    confidence: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"uri": self.uri, "source": self.source, "confidence": self.confidence}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Document:
        return cls(
            uri=str(raw.get("uri", "")),
            source=str(raw.get("source", "")),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
        )

    @property
    def path(self) -> str:
        if self.uri.startswith("file://"):
            return unquote(urlparse(self.uri).path)
        return self.uri


@dataclass
class Adapter:
    """What is known about how one application reveals its documents."""

    #: wm_class or desktop id this applies to.
    app: str
    #: Which sources to try, in order. Empty means tier 0: this app gets no document.
    sources: tuple[str, ...] = ()
    #: A regex with a named group ``doc`` applied to the window title, for the ``title`` source.
    title_pattern: str | None = None
    #: Arguments to ignore when reading a command line (``--gapplication-service`` and friends).
    ignore_args: tuple[str, ...] = ()

    def title_document(self, title: str) -> str | None:
        if not self.title_pattern or not title:
            return None
        match = re.search(self.title_pattern, title)
        if not match:
            return None
        return match.groupdict().get("doc") or None


#: The applications with rules today. Short on purpose: an application with no entry is restored
#: with no document rather than with a guessed one, which is the behaviour `docs/limitations.md`
#: promises.
ADAPTERS: dict[str, Adapter] = {
    "org.gnome.TextEditor": Adapter(
        app="org.gnome.TextEditor",
        sources=("cmdline", "recent-files"),
    ),
    "org.gnome.Nautilus": Adapter(
        app="org.gnome.Nautilus",
        sources=("fd", "recent-files"),
        ignore_args=("--gapplication-service",),
    ),
    "Soffice": Adapter(
        app="Soffice",
        sources=("cmdline", "app-history"),
        # "Thesis — LibreOffice Writer": the part before the em dash is the document name, which
        # is only a *name*, so it ranks below the picklist that gives a full path.
        title_pattern=r"^(?P<doc>.+?)\s+[—-]\s+LibreOffice",
    ),
    "libreoffice": Adapter(app="libreoffice", sources=("cmdline", "app-history")),
    "Code": Adapter(app="Code", sources=("cmdline",)),
    "VSCodium": Adapter(app="VSCodium", sources=("cmdline",)),
    "org.gnome.Evince": Adapter(app="org.gnome.Evince", sources=("cmdline", "recent-files")),
    "org.gnome.Loupe": Adapter(app="org.gnome.Loupe", sources=("recent-files",)),
    "org.gnome.gedit": Adapter(app="org.gnome.gedit", sources=("cmdline", "recent-files")),
}


def adapter_for(wm_class: str, app_id: str) -> Adapter | None:
    """The adapter for an application, or ``None`` — which means tier 0."""
    for key in (wm_class, app_id, app_id.removesuffix(".desktop")):
        if key and key in ADAPTERS:
            return ADAPTERS[key]
    return None


def is_document_path(path: str) -> bool:
    """Whether a path looks like something the user opened, rather than plumbing."""
    if not path or not path.startswith("/"):
        return False
    if path.startswith(_IGNORED_PREFIXES):
        return False
    return not path.endswith(_IGNORED_SUFFIXES)


def to_uri(path: str) -> str:
    return path if "://" in path else Path(path).as_uri()


@dataclass
class Context:
    """Everything the sources may read, injected so this module stays testable and pure."""

    #: argv of the window's process.
    cmdline: list[str] = field(default_factory=list)
    #: Targets of the process's open file descriptors.
    open_files: list[str] = field(default_factory=list)
    #: ``(uri, app_name, timestamp)`` from the freedesktop recent store, newest first.
    recent: list[tuple[str, str, float]] = field(default_factory=list)
    #: URIs from the application's own history, newest first.
    app_history: list[str] = field(default_factory=list)
    #: When the window was captured, for correlating with the recent store.
    captured_at: float = 0.0
    #: Whether a path still exists. Injected because a snapshot may be read on another machine.
    exists = staticmethod(lambda path: Path(path).exists())


def _from_cmdline(adapter: Adapter, context: Context) -> list[str]:
    found = []
    for argument in context.cmdline[1:]:
        if argument in adapter.ignore_args or argument.startswith("-"):
            continue
        if argument.startswith("file://"):
            found.append(argument)
        elif is_document_path(argument) and context.exists(argument):
            found.append(to_uri(argument))
    return found


def _from_fds(context: Context) -> list[str]:
    return [to_uri(path) for path in context.open_files if is_document_path(path)]


def _normalise(name: str) -> str:
    """Reduce an application name to something comparable across the ways it is spelled.

    The recent store records whatever each application calls itself: on this machine the same
    concept appears as ``gnome-text-editor``, ``org.gnome.Nautilus``, ``snap.firefox`` and
    ``Image Viewer``. Stripping everything but letters and digits makes ``gnome-text-editor`` and
    ``org.gnome.TextEditor`` comparable, which no exact match would.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _same_app(app_name: str, keys: tuple[str, ...]) -> bool:
    left = _normalise(app_name)
    if not left:
        return False
    for key in keys:
        right = _normalise(key)
        if right and (right in left or left in right):
            return True
    return False


def _from_recent(
    keys: tuple[str, ...], context: Context, window_seconds: float = 8 * 3600
) -> list[str]:
    """Recent documents this application opened, most recent first.

    Correlated by application *and* by time: an entry from three days ago says nothing about the
    window that is open now, and offering it would be worse than offering nothing.
    """
    matches = []
    for uri, app_name, timestamp in context.recent:
        if not _same_app(app_name, keys):
            continue
        if context.captured_at and timestamp and context.captured_at - timestamp > window_seconds:
            continue
        matches.append(uri)
    return matches


def documents_for(
    wm_class: str,
    app_id: str,
    title: str,
    context: Context,
    *,
    limit: int = 4,
) -> list[Document]:
    """The documents a window is showing, best source first."""
    adapter = adapter_for(wm_class, app_id)
    if adapter is None:
        return []  # tier 0: no adapter, no guess

    keys = tuple(key for key in (app_id.removesuffix(".desktop"), wm_class, adapter.app) if key)
    found: list[Document] = []
    seen: set[str] = set()

    for source in adapter.sources:
        if source == "cmdline":
            uris = _from_cmdline(adapter, context)
        elif source == "fd":
            uris = _from_fds(context)
        elif source == "recent-files":
            uris = _from_recent(keys, context)
        elif source == "app-history":
            uris = list(context.app_history)
        elif source == "title":
            name = adapter.title_document(title)
            uris = [name] if name else []
        else:
            continue

        for uri in uris:
            if uri in seen:
                continue
            seen.add(uri)
            found.append(Document(uri=uri, source=source, confidence=CONFIDENCE.get(source, 0.3)))
            if len(found) >= limit:
                return found

    # Title parsing is the last resort and only ever contributes a *name*, so it is used only when
    # nothing else found anything at all — a name cannot be reopened, but it can be shown to the
    # user in the review step, which is better than restoring the app blank with no explanation.
    if not found and adapter.title_pattern:
        name = adapter.title_document(title)
        if name:
            found.append(Document(uri=name, source="title", confidence=CONFIDENCE["title"]))
    return found
