# The shared core: `org.gnome.SessionCore`

The first answered open question in `PLAN.md` asked how `restore-wss` should relate to
`gnome-tasks`, which needs the same window capture, the same placement and the same adapters for a
different purpose. The answer chosen was **(c): extract the shared capture/restore core into a
component both use.** This is that component, and this document is the boundary.

## What is shared, and what is not

The reusable piece is the **compositor-side half**: the GNOME Shell extension in `src/extension/`,
which owns `org.gnome.SessionCore` on the session bus. Its interface talks about windows, monitors
and workspaces, and about nothing else — there is no mention of snapshots, reboots, tasks or
policy anywhere in it:

| Method | What it does |
| --- | --- |
| `Ping` | api version and extension version |
| `ListWindows` | every managed window, with app id, pid, geometry, workspace, monitor connector, and the GTK/Wayland identifiers |
| `GetLayout` | workspace count, active workspace, names, dynamic-workspaces setting, and every monitor with its connector and EDID |
| `EnsureWorkspaces` / `ActivateWorkspace` | create workspaces, switch to one |
| `PlaceWindow` / `GetPlacementVerdict` | move/resize/maximise a window; ask separately whether it took |
| `LaunchApp` / `ExpectWindow` / `GetLaunchReport` | start an application (or expect one somebody else starts) and place the window it produces |
| `WindowsChanged` (signal) | something changed, coalesced |

Everything else in this repository is *not* shared: the snapshot, the storage format, the matcher,
the command policy, the VPN handling and the CLI are all `restore-wss`, and they all live outside
the compositor.

## Why the split is where it is

It is not an aesthetic preference. This code runs inside `gnome-shell`, where a blocking call
freezes the desktop and an unhandled exception can take the session down. So the extension:

* holds no state worth losing,
* writes no files,
* spawns no processes,
* makes no decisions.

That is also exactly what makes it reusable. A different consumer — a task switcher, a tiling
helper, a screen-layout tool — wants the same six verbs and none of `restore-wss`'s opinions.

`ExpectWindow` is the clearest example of the boundary paying for itself. Restoring a terminal
needs a process spawned with a working directory and a command, which must not happen inside the
compositor; so the daemon spawns it and tells the extension to *expect* the window and place it.
The compositor half gained one small, general method instead of a terminal feature.

## Using it from another project

1. Install the extension (`session-core@restore-wss.patxi`) and enable it.
2. Watch for the name `org.gnome.SessionCore` on the session bus; treat its absence as "no window
   information available" rather than as an error — that is the normal state before the user has
   logged out and back in.
3. Call `Ping` and check the api version (currently `1`) before relying on anything else.
4. Read `src/restore_wss/protocol.py` for the interface, and `src/restore_wss/busclient.py` for a
   ~100-line client that is a reasonable thing to copy.

The two copies of the interface XML — Python and JS — are kept in step by
`tests/unit/test_protocol_parity.py`, which fails if they drift. The extension is loaded by
`gnome-shell` as plain files with no build step, which is why there are two copies rather than one
generated from the other.

## What `gnome-tasks` would have to do to adopt it

Stated honestly, because it has not been done: `gnome-tasks` is complete and its extension is its
own. Adopting this core would mean replacing its `org.gnome.Tasks.Shell` service with a client of
`org.gnome.SessionCore` — the methods correspond nearly one to one, and its `windowIntrospect.js`
and `placement.js` were the starting point for this project's. The parts of it that would *not*
move are exactly the parts that should not: its task model, its indicator, its preferences and its
policies.

What this project deliberately does not do is modify `gnome-tasks`. It is finished and released;
the shared component is offered, not imposed.
