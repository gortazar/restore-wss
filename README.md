# restore-wss — put the workspaces back the way they were

You shut the laptop down with seven workspaces full of work: a thesis in LibreOffice, an editor with
a project open, a terminal in a repo running an agent, another holding an `ssh` session, a VPN up.
After the reboot the desktop is empty. `restore-wss` puts it back.

It is two programs on GNOME/Wayland: a **Shell extension** that watches and places windows from
inside the compositor, and a **Python daemon** that owns the snapshot on disk and does the work.
The snapshot is written continuously, so an unclean power-off is the normal case rather than the
one that loses everything.

**Status: in development.** This README will open with the one-line installer once there is a
release to install; until then, see [Development](#development).

## Development

```console
$ nix develop        # dev shell: python, pytest, gjs, dbus, lint
$ make test          # the blocking suite
$ nix flake check    # the same, hermetically — what CI runs
```

## Documentation

| Document | What it covers |
| --- | --- |
| [docs/similar-tools.md](docs/similar-tools.md) | Prior art: what exists, what it cannot do, what this takes from it |
| [docs/platform-findings.md](docs/platform-findings.md) | What GNOME 46/Wayland actually exposes, measured on the target machine |

## Licence

GPL-2.0-or-later. The extension half has to be, and the rest matches it.
