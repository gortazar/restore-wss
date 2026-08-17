# Application adapters: the tier model, and how to add one

"Which document is this window showing?" has no general answer on Linux. So instead of one
mechanism pretending to cover everything, applications sit on a ladder, and every answer carries a
confidence.

## The tiers

| Tier | What it means | What restore does |
| --- | --- | --- |
| **0** | The application only. No adapter, no rule, no guess. | Launches the app with no arguments. |
| **1** | The document is recovered by introspection — from the process, from a recent-file store, or from a declared title pattern. | Launches the app with the document's URI. |
| **2** | The application reports its own state, through something installed alongside it. | **Real since v0.2**: Firefox, through the add-on and its native-messaging host. See the worked example below. |

**Tier 0 is a real answer, not a failure.** An application with no entry in the table is restored
blank rather than with a guessed document, because opening the wrong file is worse than opening
none.

## The tier-1 sources, in order of preference

| Source | Confidence | Works when | Fails when |
| --- | --- | --- | --- |
| `cmdline` | 1.0 | the app was launched with its document (`gnome-text-editor notes.txt`) | the app is D-Bus-activated — `nautilus --gapplication-service` has no document in it |
| `app-history` | 0.8 | the app keeps its own recent list (LibreOffice) | most apps do not |
| `fd` | 0.7 | the app holds the document open — Nautilus holds a descriptor on the folder it shows | a GTK editor reads the file and closes it |
| `recent-files` | 0.6 | the app writes to `~/.local/share/recently-used.xbel` — 12 applications did on this machine | LibreOffice, browsers and Electron apps do not, or do so under names that need matching |
| `title` | 0.5 | a pattern is *declared for that application* | never applied globally; yields a name, not a path, so the document cannot actually be reopened from it |

`recent-files` is correlated by application **and** by recency: an entry from three days ago says
nothing about the window that is open now. Application names in that file are inconsistent
(`gnome-text-editor`, `org.gnome.Nautilus`, `snap.firefox`, `Image Viewer`), so matching strips
everything but letters and digits and asks whether either name contains the other.

## Worked examples, one per shape

### A plain GTK application — `gnome-text-editor` (tier 1, `cmdline`)

```python
"org.gnome.TextEditor": Adapter(
    app="org.gnome.TextEditor",
    sources=("cmdline", "recent-files"),
),
```

The document is in argv, so the answer is exact. `recent-files` is the fallback for the case where
the editor was started from the file manager and the process was reused.

### A D-Bus-activated application — Nautilus (tier 1, `fd`)

```python
"org.gnome.Nautilus": Adapter(
    app="org.gnome.Nautilus",
    sources=("fd", "recent-files"),
    ignore_args=("--gapplication-service",),
),
```

Its command line is useless — that is what `ignore_args` documents — but it holds a descriptor on
the directory it is showing, which `fd` picks up.

### An application with its own history — LibreOffice (tier 1, `app-history`)

```python
"Soffice": Adapter(
    app="Soffice",
    sources=("cmdline", "app-history"),
    title_pattern=r"^(?P<doc>.+?)\s+[—-]\s+LibreOffice",
),
```

LibreOffice does **not** write to the freedesktop recent store. It keeps a picklist of its own in
`registrymodifications.xcu`, as `<node oor:name="file:///…">` entries under
`HistoryInfo['PickList']/ItemList` — 25 of them on this machine. That file is megabytes of
unrelated settings, so it is read once per capture and only when an application that needs it is
on screen.

The title pattern is the last resort and only ever yields a *name*: "Thesis" cannot be reopened, so
restore says "the document is only known by name (Thesis)" in the review text and lowers the
action's confidence instead of pretending.

### A browser — Firefox (tier 2, and the worked example for it)

The only tier-2 application today, and worth reading as the template for what tier 2 costs.

**What reports the state.** A WebExtension with the `tabs` permission
(`src/browser-extension/`) sends every non-private window's tabs to a native-messaging host
(`src/native-host/restore-wss-firefox-host.py`), which drops them in a file the daemon reads. Not
D-Bus: a host executed by snap Firefox inherits the browser's AppArmor confinement, whose session-bus
rules are a per-name allow-list (`docs/browser-extensions-research.md` §1).

**What happens without it.** Tier 2 degrades to a *file* rather than to nothing: Firefox's own
`recovery.jsonlz4` is read directly, giving windows, tabs, pinned and selected state and geometry
with no add-on and no permission — minutes stale instead of live, and labelled as such in the
snapshot (`source: "session-file"`).

**What is different about tier 2.** Two things this project had not had to deal with before:

1. **Correlation.** A document belongs to the window whose process it was read from; a *tab set*
   belongs to a browser window that has no connection to the compositor window except its title.
   That is a heuristic with a confidence, and it refuses rather than guesses
   (`src/restore_wss/browsercorrelate.py`).
2. **The application restores itself.** Firefox's own session restore stays on, so restore is
   reconciliation: fill in what it did not bring back, never duplicate what it did
   (`src/restore_wss/browserrestore.py`).

**What it does not recover:** per-tab back/forward history, scroll position and form state. Reading
those needs a content script in every page — a far larger permission for a much smaller return.

### A terminal — `gnome-terminal` (its own mechanism entirely)

Terminals do not go through this table at all; they have their own path
(`src/restore_wss/terminals.py`), because what they are showing is not a document but a working
directory and a running program. See [platform-findings.md](platform-findings.md) §3 for how tabs
and foreground jobs are read out of `/proc`, and [limitations.md](limitations.md) for the command
policy that decides what is re-run.

## Adding an application

One entry in `ADAPTERS` in `src/restore_wss/documents.py`, and one test:

```python
"org.gnome.Evince": Adapter(app="org.gnome.Evince", sources=("cmdline", "recent-files")),
```

Rules for a new entry, all of which exist because the alternative is opening the wrong file:

1. **Name the sources explicitly.** No adapter should say "try everything".
2. **Only add `title` with a pattern you have tested against real titles**, and remember titles are
   localised and user-configurable.
3. **Never add a source that reads a process tree** for a non-terminal application. Recording
   somebody's command line is a deliberate act, and the terminal path is where it is deliberate.
4. **Add a test** in `tests/unit/test_documents.py`. It costs four lines and it is the only way the
   confidence values stay meaningful.

## What the confidence is for

It is not a probability. It is an ordering with gaps wide enough to act on: the review step
pre-ticks what scores at least 0.9 and marks the rest with a `?`, and a document known only by name
drags its action's confidence down so the user looks at it. `restore-wss restore --dry-run` prints
the whole list, marks and all.
