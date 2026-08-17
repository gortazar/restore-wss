# The files in `~/.restore-wss/`

Two files, both human-readable and hand-editable on purpose. They are yours: local, plain, and
deletable.

```
~/.restore-wss/
├── config.toml            your settings
└── state/                 created mode 0700
    ├── session.json       the current snapshot
    └── session.prev.json  the one before it
```

## `state/session.json`

A snapshot of one session at one instant. Written continuously by the daemon, not at logout —
that is the whole design (see [similar-tools.md](similar-tools.md) §1).

### How it is written

Never in place. Every write is: a temp file in the same directory → `fsync` → `rename(2)` over the
target → `fsync` on the directory. `rename(2)` is atomic, so a reader sees either the whole old
file or the whole new one, never a mixture. Before the rename, the current file is copied to
`session.prev.json`.

Reading tries `session.json` first and falls back to `session.prev.json` if it will not parse.
That covers the case a rename cannot: a file that is valid JSON and wrong, or damaged by something
other than a torn write. The cost of a fallback is a few minutes of capture, not the session.

`state/` is created mode `0700` and the files `0600`. This file records which documents you open
and which commands you run; on a shared machine that is nobody else's business.

### Shape

```jsonc
{
  "schema": 1,                      // bumped when an older version could not read this file
  "captured_at": 1786918000.123,    // unix time
  "boot_id": "6f7a…",               // /proc/sys/kernel/random/boot_id at capture time
  "workspace_count": 4,
  "active_workspace": 1,
  "workspace_names": ["Writing", "Code", "", ""],
  "monitors": [
    {
      "connector": "eDP-1",         // the identity that survives a replug, from DisplayConfig
      "vendor": "AUO", "product": "B140HAN", "serial": "0x00",
      "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
      "scale": 1.0,
      "primary": true
    }
  ],
  "windows": [ /* see below */ ],
  "vpn": [
    {"uuid": "8f2c…", "name": "work", "type": "vpn"}   // identity only, never a credential
  ]
}
```

`boot_id` is what makes an automatic restore offer non-spurious: if the snapshot's boot id is the
current one, the session was not lost to a reboot and there is nothing to offer.

### A window record

```jsonc
{
  "id": "763757057",                // per-session compositor handle; useless across a reboot
  "wm_class": "org.gnome.TextEditor",
  "title": "notes.txt — Text Editor",
  "app_id": "org.gnome.TextEditor.desktop",   // what restore launches
  "pid": 4242,
  "workspace": 1,                   // 0-based, as the compositor counts
  "monitor": "eDP-1",               // connector name, not an index
  "frame": {"x": 10, "y": 20, "width": 800, "height": 600},  // absent = geometry unknown
  "maximized": false, "fullscreen": false, "minimized": false,
  "stacking": 3,                    // 0 = bottom of the stack

  // Present only when the application has an adapter (docs/app-adapters.md).
  "documents": [
    {"uri": "file:///home/user/notes.txt", "source": "cmdline", "confidence": 1.0}
  ],

  // Present only for declared terminal emulators.
  "terminal": {
    "server_pid": 177551,
    "tabs": [
      {
        "cwd": "/home/user/git/my-repo",
        "tty": 34824,
        "session": 177788,
        "shell": ["bash", "-i"],
        "command": ["claude", "-r"],   // the foreground job, redacted
        "redacted": []                  // indices of arguments replaced at capture time
      }
    ]
  },

  // Present only for declared browsers (v0.2). `source` says which of the two readers produced
  // it, because "the browser told us" and "we read its session file, which may be minutes stale"
  // are different claims; `confidence` is how sure the correlation between this compositor window
  // and that browser window is (docs/browser-extensions-research.md §6).
  "browser": {
    "family": "firefox",
    "version": "142.0",
    "profile": "cqdb58zj.default",       // the directory name, never the path
    "window_id": "7",                    // the browser's own id; per-run only
    "source": "extension",               // or "session-file"
    "confidence": 0.86,                  // omitted when it is 1.0
    "reason": "title 0.86; reported by the browser extension",
    "tabs": [
      {"url": "https://gnome.org/", "title": "GNOME", "active": true},
      {"url": "https://mail.example.com/", "title": "Inbox", "pinned": true}
    ]
  },

  // Reserved. On GNOME 47+ an application that restores itself through
  // xdg-session-management-v1 is recorded here and left alone. Always empty on GNOME 46, which
  // does not have the protocol (docs/platform-findings.md §1).
  "session_protocol": ""
}
```

**Absent means unknown.** A window with no `frame` is a window whose geometry was never known —
the compositor reports `0x0` for up to a second after a window appears, and recording that would
put windows at the origin at the size of nothing.

**Unknown fields survive.** Anything this version does not model — a field written by a newer
release, a note added by hand, a block written by a tier-2 adapter — is preserved verbatim through
a read/write cycle. There is a test for it.

### Versioning and migration

`schema` is bumped only when a snapshot written by the new version cannot be read by the old one.
Adding a field is not a bump, because unknown fields are preserved and missing ones have honest
defaults.

| `schema` | Version | Change |
| --- | --- | --- |
| 1 | 0.1 | first format |
| 1 | 0.2 | added the per-window `browser` block. **Not a bump**: a v0.1 snapshot has no block and restores exactly as it did, and a v0.2 snapshot read by v0.1 keeps the block as an unknown field. There is a test for each direction |

A snapshot with a *higher* schema than this build understands is still read — the fields it knows
are used and the rest are carried through — because refusing to read it would be worse than
restoring less of it.

## `config.toml`

Every setting is optional and every default is the conservative answer. A malformed file is
reported, never fatal: a typo must not silently stop the machine snapshotting.

```toml
[capture]
paused = false              # stop capturing without stopping the daemon
exclude_apps = []           # wm_class or desktop id, never recorded
exclude_paths = []          # path prefixes; documents and working directories under them are dropped
# terminals = ["gnome-terminal-server", "Alacritty"]   # which apps get their process tree read

[browsers]
enabled = true              # capture tabs at all; off leaves the rest of restore-wss working
store = "urls"              # "urls" (url + title) | "titles" | "none" (window shape only)
exclude_urls = []           # substrings or globs; matching tabs are never recorded
# browsers = ["firefox"]    # which applications are browsers

[commands]
policy = "whitelist"        # never | whitelist | always
allow = []                  # programs added to the built-in allow-list
deny = []                   # programs never re-run, whatever the policy
redact_options = []         # extra option names whose values are redacted at capture time

[restore]
at_login = false            # offer a restore at the first login after a reboot
unattended = false          # and do it without asking (only with at_login)
```

See [limitations.md](limitations.md) for what each command policy means in practice, and why
`whitelist` is the default.
