# Prior art: who has tried to restore a desktop session, and how far they got

Written before any code, from **reading the actual sources and running things on the target
machine**, not from recollection. Every claim here is either a file-and-line reference into a
checkout of the tool, a query against its upstream forge, or a command whose output is reproduced
below. Where something is not established, it says so.

Target machine, referred to throughout as *this machine*:

```
GNOME Shell 46.0 · Mutter 14 (libmutter-14.so.0) · Wayland · Ubuntu 24.04
```

The tools are grouped by the question they answer, because they do not compete with each other so
much as solve different halves of the problem. The last section is the shopping list: what
`restore-wss` takes from each, and what it deliberately does differently.

---

## 1. The X11 session-management tradition (XSMP)

### What it is

XSMP — the X11R6 session management protocol — is a negotiation between a session manager and
*participating clients*: at logout the manager tells each client to save its state; the client
writes its own state somewhere and hands back a **restart command**; at next login the manager runs
those commands. `ksmserver`'s own README is refreshingly blunt about the take-up:

> Unfortunately, there aren't that many of them. To be precise, I have never seen a single
> commercial application that supports it and even within the official X11R6 distribution,
> 'xclock' is the only exception.
> — [`ksmserver/README`](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/ksmserver/README)

For clients that do *not* participate, both GNOME and KDE fall back to the window manager saving
window geometry and re-applying it to windows that look like the ones it saw before.

### GNOME: `gnome-session`'s `saved-session`, and the state of it on this machine

`gnome-session` supports saving the current session as a set of `.desktop` files in
`~/.config/gnome-session/saved-session/`, gated by the GSettings key
`org.gnome.SessionManager auto-save-session`. All of that machinery is still *present* in
`gnome-session-bin 46.0-1ubuntu4`:

```console
$ strings /usr/libexec/gnome-session-binary | grep -iE 'saved-session|auto-save|SmsInitialize'
SmsInitialize
auto-save-session-one-shot
auto-save-session
saved-session

$ gsettings list-recursively org.gnome.SessionManager
org.gnome.SessionManager auto-save-session false
org.gnome.SessionManager auto-save-session-one-shot false
org.gnome.SessionManager logout-prompt true
org.gnome.SessionManager show-fallback-warning true
```

And it is empty, on a machine that has been logged into daily since it was created:

```console
$ ls -la ~/.config/gnome-session/saved-session/
total 8
drwx------ 2 patxi patxi 4096 sep  8  2024 .
drwx------ 3 patxi patxi 4096 sep  8  2024 ..
```

**What it cannot do.** Three fatal things, and it is worth being precise about which is which:

1. **It is a logout hook.** State is captured when the session ends cleanly. A power cut, a kernel
   panic or a long press on the power button produces nothing at all.
2. **It restarts programs, it does not restore windows.** The saved `.desktop` files are
   `Exec=` lines. Which workspace, which monitor, what geometry: not part of it. That half was the
   X11 window manager's job, via `SM_CLIENT_ID`/`WM_CLIENT_LEADER` on X11 windows.
3. **On Wayland, the client half is gone.** XSMP is an X11 protocol; a Wayland client has no
   `SM_CLIENT_ID` and no ICE connection to the session manager. Mutter still carries the code —
   the string `Failed to a open connection to a session manager, so window positions will not be
   saved` is in `libmutter-14.so.0` — but it is about *its own* X11 session client, not about
   Wayland toplevels.

**What `restore-wss` takes from it.** Only the negative lesson, and it is the load-bearing one:
*capture cannot happen at shutdown*. This is why `restore-wss` is a daemon holding a continuously
refreshed snapshot rather than a logout hook. It also takes the shape of the fallback: a restart
command plus separately recorded window placement is the right decomposition, even if neither half
can be obtained the way XSMP obtained it.

### KDE: `ksmserver` + KWin, and what it does that GNOME does not

Plasma keeps a working, user-visible session restore: *System Settings → Session → on login,
restore previous session / restore manually saved session / start with an empty session*. Under it:

* `ksmserver` still speaks XSMP, and additionally drives KWin over a private D-Bus interface,
  [`org.kde.KWin.Session`](https://invent.kde.org/plasma/plasma-workspace/-/blob/master/ksmserver/org.kde.KWin.Session.xml):
  `setState`, `loadSession`, `aboutToSaveSession`, `finishSaveSession`, `quit`, and —
  tellingly — `closeWaylandWindows`.
* KWin persists per-window state into a `KConfig` file keyed by session id
  ([`src/sm.cpp`](https://invent.kde.org/plasma/kwin/-/blob/master/src/sm.cpp)), writing `geometry`,
  `restore`, and friends per window.
* A systemd unit, `plasma-restoresession.service`, runs the restore on next login.

**What it cannot do.** `SessionManager::storeClient(KConfigGroup &cg, int num, X11Window *c)` takes
an `X11Window`. KWin's classic session save is **X11-only**; on Wayland the corresponding entry
point is `closeWaylandWindows()`, i.e. "ask them to close politely at logout", which is a
shutdown-quality feature, not a restore feature. Plasma's Wayland answer is the new protocol in
§2, not this.

**What `restore-wss` takes from it.** The user-facing shape: *previous session / saved session /
empty*, chosen up front rather than asked about after the fact. And a specific mechanic worth
copying — restore driven by a **systemd user unit** rather than an XDG autostart entry, so it has
an ordering relationship with the graphical session rather than a race with it.

---

## 2. The standards-track answer: `xdg-session-management-v1`

This is where `PLAN.md` is out of date, and the correction matters, so it is spelled out.

**The protocol has graduated from experimental to staging, and been renamed.** What `PLAN.md` calls
`xx_session_management_v1` is now `xdg-session-management-v1`:

| wayland-protocols MR | State | Date |
| --- | --- | --- |
| `!392 experimental: Add xx-session-management-v1` | merged | 2025-05-07 |
| `!18 staging: Add xdg-session-management protocol` | merged | 2026-03-23 |
| `!497 disallow adding a toplevel twice`, `!498 add invalid_reason protocol error` | merged | 2026-04-01 |
| `!522 Clarify that removing a session destroys the session object` | open | 2026-06-24 |

```console
$ curl -s 'https://gitlab.freedesktop.org/api/v4/projects/wayland%2Fwayland-protocols/repository/tree?path=staging' | jq -r '.[].name' | grep session
xdg-session-management
```

The shape of it, from
[`staging/xdg-session-management/xdg-session-management-v1.xml`](https://gitlab.freedesktop.org/wayland/wayland-protocols/-/blob/main/staging/xdg-session-management/xdg-session-management-v1.xml)
(version 1):

* `xdg_session_manager_v1.get_session(reason, session_id)` — the client asks for a session object,
  optionally quoting a session id it was given last time.
* `xdg_session_v1` — `add_toplevel(toplevel, name)`, `restore_toplevel(toplevel, name)`,
  `remove_toplevel(name)`, `remove` (forget everything), and events `created(session_id)`,
  `restored(session_id)`, `replaced`.
* `xdg_toplevel_session_v1` — `rename(name)`, event `restored`.

So the unit of state is **a client-chosen string name per toplevel**, and the state itself is held
by the *compositor*, not by the client: the client says "this window is my window number 3" and the
compositor puts window number 3 back where it was.

**Implementations.** Mutter implemented it early and Mutter's is the interesting one for us:

| Compositor | Status |
| --- | --- |
| Mutter | `!3825 Implement XDG session management wayland protocol` merged **2024-09-14**; present in the `gnome-47` branch onwards; `!4806 wayland: Use xdg_session_management_v1 from wayland-protocols` merged 2026-07-03 |
| KWin | `!8985 wayland: Implement xdg-session-management-v1` merged **2026-04-13** |

Mutter stores the state itself, in a **GVDB** database — `meta-wayland-xdg-session-state.c` writes
a `version`, a `last-used` timestamp and a `toplevels` hash table whose entries carry `state`
(the window-state bitmask) and `floating-rect`. In other words, on GNOME 47+ the compositor already
maintains a private, per-application, crash-independent store of window geometry — for the
applications that ask for one.

**And on this machine, none of it exists.** The live compositor's registry, dumped from a real
Wayland client (`tools/wayland-globals.sh`, output committed as
`docs/probe-data/wayland-globals.txt`):

```
gtk_shell1 wl_compositor wl_data_device_manager wl_output wl_seat wl_shm wl_subcompositor
wp_fractional_scale_manager_v1 wp_presentation wp_single_pixel_buffer_manager_v1 wp_viewporter
xdg_activation_v1 xdg_wm_base zwp_idle_inhibit_manager_v1 zwp_keyboard_shortcuts_inhibit_manager_v1
zwp_linux_dmabuf_v1 zwp_pointer_constraints_v1 zwp_pointer_gestures_v1
zwp_primary_selection_device_manager_v1 zwp_relative_pointer_manager_v1 zwp_tablet_manager_v2
zwp_text_input_manager_v3 zxdg_exporter_v1 zxdg_exporter_v2 zxdg_importer_v1 zxdg_importer_v2
zxdg_output_manager_v1
```

No `xdg_session_manager_v1`, no `xx_session_manager_v1`. GNOME 46 predates Mutter's
implementation. `xdg_activation_v1` **is** there, which matters for launch matching (§6).

### The portal track

There is a parallel, non-Wayland-specific effort: a **save/restore portal**
([discussion #1698](https://github.com/flatpak/xdg-desktop-portal/discussions/1698),
[PR #1818 "Introduce save/restore portal"](https://github.com/flatpak/xdg-desktop-portal/pull/1818),
opened 2025-09-25, **still open**, last touched 2026-03-11). Its model is different and
complementary: the application saves its *own* state to a file and hands the session manager an
executable line to bring it back — so it covers TUI apps and app-internal state, where the Wayland
protocol covers toplevel geometry. Its two companion MRs are both still **drafts**:

| MR | State |
| --- | --- |
| `gnome-session!162 Draft: session-save: Implement xdg-desktop-portal backend` | opened, updated 2026-01-20 |
| `gtk!8980 Draft: gtkapplication-dbus: Use save/restore portal` | opened, updated 2026-01-23 |

**Verdict, and it is the go/no-go this section exists for.** The standards answer is real, is
moving, and is the right long-term home for this problem — and it is unavailable here twice over:
absent from the compositor on the target machine, and, even where present, **opt-in per
application**. GTK's client-side support is a draft; LibreOffice, VS Code/Codium, Electron apps and
terminals will not opt in for years. `restore-wss` is therefore built on introspection, and
`docs/state-schema.md` reserves a per-window `session_protocol` field so that, on a machine where
the compositor and the app both support it, `restore-wss` can record "this one restores itself" and
stay out of the way.

---

## 3. GNOME Shell extensions in this space

Four were read. They divide sharply into *"remembers geometry within a session"* and
*"saves a session to disk"*, and only the second class is trying to do what this idea asks.

### `smart-auto-move` (khimaros) and `SmartAutoMoveNG` (ChrisLauinger77)

The best-documented **window identity heuristic** available, and worth reading in full. Both forks
share the algorithm; the NG fork adds Wayland/dynamic-workspace handling and is the maintained one.

From [`lib/state-matcher.js`](https://github.com/khimaros/smart-auto-move/blob/master/lib/state-matcher.js):

```js
compareDetails(details1, details2) {
    if (details1.wm_class !== details2.wm_class) return 0.0   // hard gate
    if (title1 === title2) return 1.0                         // exact title wins outright

    const hist1 = this.titleToHist(title1)                    // 95-char histogram, normalised
    const hist2 = this.titleToHist(title2)                    //   by title length
    let dist = 0
    for (let i = 0; i < hist1.length; i++) dist += Math.abs(hist1[i] - hist2[i])   // L1 distance
    return Math.max(0, 1 - dist / 2.0)                        // TITLE_SIMILARITY_MAX_DIST
}
```

Around that core sit the parts that make it work in practice, all in `DEFAULT_CONFIG`:

| Knob | Value | What it is for |
| --- | --- | --- |
| `DEFAULT_MATCH_THRESHOLD` | `0.8` | below this, no match is claimed |
| `MIN_SCORE_SPREAD` | `0.6` | if the best and second-best candidates are too close, refuse rather than guess |
| `AMBIGUOUS_SIMILARITY_THRESHOLD` | `0.95` | two saved windows this similar are treated as interchangeable |
| `TITLE_LEN_PENALTY_*` | `8 / 0.5 / 0.5` | a much shorter title than the saved one is probably the app's placeholder, not the document |
| `SPECIFIC_MATCH_BOOST` | `1.1` | prefer specific-to-specific over generic-to-generic |
| `MIN_SPECIFIC_TITLE_LENGTH` | `15` | what counts as a "specific" title |
| `SETTLE_IDLE_TIMEOUT` / `SETTLE_MAX_WAIT` | `500 / 2500 ms` | a window is matched once its title has stopped changing |
| `GENERIC_TITLE_EXTENDED_WAIT` | `15000 ms` | if the title still looks generic, wait much longer before giving up |
| `DEBOUNCE_MS` | `500` | per-window, per-event-type debounce on move/resize |

It also solved the monitor-identity problem the same way this plan proposes: `updateOrAddConfig()`
stores one config **per connector name**, with the rect converted to monitor-relative coordinates,
and refuses to save at all if it cannot get a connector for the monitor.

**What it cannot do.** It restores *geometry for windows that appear*; it never launches anything.
Nothing brings the applications back after a reboot, and no document, folder or command is
recorded. It is a "windows should reopen where I left them" tool, and an excellent one.

**What `restore-wss` takes from it.** The matcher, close to wholesale: `wm_class` as a hard gate, a
character-histogram title distance, a match threshold *and a minimum spread* so an ambiguous match
is refused rather than guessed, the title-length penalty, the settle-then-match timing, and
per-connector monitor-relative geometry. Ported to Python in the daemon, where it is pure logic and
can be unit-tested against fixtures — which is exactly the part of a heuristic that needs tests.

### `Another Window Session Manager` (nlpsuge) — `another-window-session-manager@gmail.com`

The closest existing thing to this idea on GNOME/Wayland: an extension that saves the open windows
as a named session and restores them later, optionally at startup.

Its saved model ([`model/sessionConfig.js`](https://github.com/nlpsuge/gnome-shell-extension-another-window-session-manager/blob/master/model/sessionConfig.js))
is close to what this plan wants to record, which is a useful confirmation that the field list is
the natural one:

```
window_id, desktop_number, pid, username, window_position{provider,x_offset,y_offset,width,height},
window_title, app_name, wm_class, wm_class_instance, cmd, process_create_time,
window_state{is_sticky,is_above,meta_maximized}, desktop_file_id, desktop_file_id_full_path,
monitor_number, is_on_primary_monitor, fullscreen, minimized, is_focused, compositor_type
+ session-level: active_workspace_index, n_workspace, focused_window
```

**How it captures a command line.** From inside the compositor process, it shells out to `ps`
(`utils/subprocessUtils.js: getProcessInfo()`), parses `lstart %cpu %mem args`, and keeps
`args` as `cmd`.

**How it restores one.** Preferentially through `Shell.App#launch()` from the desktop file; if there
is no desktop file it falls back to running the captured command line through a template
(`template/launch-app.sh`):

```sh
if ! pgrep -f "${cmdString}" | grep -v "$$"; then
  ${cmdString} > /dev/null &
  echo $! >&1
else
  exit 79     # already running
fi
```

**What it cannot do — and this is the important entry in this whole document.** That is a captured
command line, interpolated into a shell script, run unreviewed. There is no allow-list, no
redaction of arguments, and no confirmation step; `pgrep -f` deduplication is the only safety
behaviour. Its saves are also **manual or on a timer**, from inside the compositor, and its
document recovery is whatever happened to be on the command line. It has no notion of confidence.

**What `restore-wss` takes from it.** The field list (independently arrived at, so a good sanity
check), the "launch via desktop file first, command line only as a fallback" ordering, and the
already-running check as an idempotency primitive. What it deliberately does *not* take: running
captured command lines through a shell, unreviewed, from inside the compositor. `restore-wss` runs
commands from the daemon, as argv with no shell, only when the program is on an allow-list or the
user has confirmed it, and never when the command contains a redaction.

### `Window State Manager` (kishorv06) — `window-state-manager@kishorv06.github.io`

15 618 downloads, GNOME 42–50. Saves window state periodically and re-applies it when the monitor
arrangement changes. The whole identity model is one line
([`lib/extension/windowstate.js`](https://github.com/kishorv06/window-state-manager)):

```js
windowId__state.set(window.get_id(), new Window(window));
...
if (windowId__state.has(window.get_id())) windowId__state.get(window.get_id()).restore(window);
```

**What it cannot do.** `Meta.Window.get_id()` is a per-session handle. Nothing survives the window
closing, let alone a reboot. It is the right tool for "the projector renumbered my monitors" and
structurally incapable of being the tool for this idea.

**What `restore-wss` takes from it.** A negative control: it is the cheapest possible answer to
window identity, and it demonstrates precisely why the histogram matcher exists.

Also seen on extensions.gnome.org and not read in depth, because they are the same class:
`all-windows-srwp@jkavery.github.io` (save/restore window positions across suspend/resume) and
`restore-geometry@upsuper.org` (restore a window's previous geometry when it reopens).

---

## 4. The tiling-WM tools: the ones that actually restore *programs*

### `i3-resurrect` (JonnyHaystack, 437★)

The closest prior art for the half of this idea that GNOME tools ignore. It walks the i3 tree,
takes each window's PID, and reads the process:

```python
procinfo = psutil.Process(pid)                       # /proc/<pid>
command = get_window_command(con["window_properties"], procinfo.cmdline(), exe)
...
if con["window_properties"]["class"] in terminals:   # config: ["Gnome-terminal", "Alacritty"]
    working_directory = procinfo.children()[0].cwd() # the shell's cwd, not the emulator's
else:
    working_directory = procinfo.cwd()
```

— [`i3_resurrect/programs.py`](https://github.com/JonnyHaystack/i3-resurrect/blob/master/i3_resurrect/programs.py)

Two design details worth stealing outright. First, **terminals are a declared class of application**
whose working directory comes from the *first child process*, not from the window's own process —
the same realisation this plan reached from the other direction. Second, `window_command_mappings`
is a scored rule list keyed on window class, so a per-app override can replace a useless captured
command line with a sensible one.

**What it cannot do.** Restore is:

```python
i3.command(f'exec "cd \\"{working_directory}\\" && {command}"')
```

Every captured command line is replayed, through a shell, unconditionally. It is also X11-only
(`xprop _NET_WM_PID` to get the PID), and it saves per-workspace on demand rather than
continuously, so a crash loses whatever changed since the last explicit `i3-resurrect save`.

**What `restore-wss` takes from it.** The `/proc` walk — `cmdline`, `cwd`, and *the child process's*
cwd for terminals — plus the idea of per-app command mappings. Not the unconditional replay.

### `i3-restore` (jdholtz, 80★)

The younger, more careful sibling, and the one that supplies the safety precedent this plan wants.
Its `config.json` has an explicit, opt-in list of the programs it is willing to bring back inside a
terminal:

```json
"subprocesses": [
    { "name": "cmus", "launch_command": "{command} && i3-msg kill" },
    { "name": "codex" },
    { "name": "ipython" },
    { "name": "man" },
    { "name": "nvim", "exclude_args": ["--embed"] }
],
"terminals": [ { "class": "Alacritty", "command": "alacritty" }, { "class": "kitty", "command": "kitty" } ]
```

Anything not named there is not restored; a named program can have specific arguments filtered out.
It also ships automatic saving (`utils/automatic_saving.bash`) rather than relying on a logout hook.

**What it cannot do.** i3/X11 only; the allow-list is opt-in with an empty default, so out of the
box it restores no in-terminal programs at all.

**What `restore-wss` takes from it.** The `subprocesses` model almost exactly: an allow-list keyed
on program name, with per-program argument filtering, as the default policy — seeded (per the
answered open question in `PLAN.md`) with the obviously safe ones rather than left empty.

---

## 5. `tmux-resurrect` / `tmux-continuum`: the safety precedent

`tmux-resurrect` restores tmux sessions, and its treatment of "should I re-run this?" is the most
battle-tested version of the policy this plan adopts. From
[`scripts/variables.sh`](https://github.com/tmux-plugins/tmux-resurrect/blob/master/scripts/variables.sh):

```sh
default_proc_list='vi vim view nvim emacs man less more tail top htop irssi weechat mutt'
```

and [`scripts/process_restore_helpers.sh`](https://github.com/tmux-plugins/tmux-resurrect/blob/master/scripts/process_restore_helpers.sh):

* `_process_should_be_restored()` returns false unless the process is on the restore list —
  or the user has opted in globally with `@resurrect-processes ':all:'`.
* Per-program **strategies** may rewrite the command (`vim` → `vim -S` to reload the session file),
  so "restore this program" is not always "run exactly this argv".
* A pane that **already exists** is registered as such and its process is not restored: restore is
  idempotent by construction.
* Restoration is `tmux send-keys` into the pane — the command is typed, visibly, into a live shell.

`tmux-continuum` adds the other half this plan needs: **automatic saving on an interval**
(15 minutes by default) rather than at exit, precisely so a crash is survivable.

**What it cannot do.** Nothing about windows, workspaces, or GUI applications; it lives entirely
inside tmux, and what it restores is a layout of shells plus a whitelisted command per pane.

**What `restore-wss` takes from it.** Three things: the **conservative default whitelist** (the
default answer to "re-run this?" is *no, unless it is a known-safe program*), the **per-program
rewrite hook**, and **idempotency by registering what already exists** before restoring anything.
Also the cadence argument for `tmux-continuum`: periodic saving is not a nicety, it is the only
thing that survives a crash.

---

## 6. `xsession-manager` (nlpsuge)

The X11 predecessor of Another Window Session Manager, by the same author, in Python. It uses
`wmctrl`/Xlib for the window list and `psutil` for the process side (`sd.cmd = process.cmdline()`),
and restores by matching a saved `cmd` against currently running processes before deciding to
launch. Its `_is_same_cmd()` is a small catalogue of the traps in that comparison:

```python
first_cmdline = [c[0] for c in groupby(first_cmdline)
                 if (c[0] != "--gapplication-service" and not c[0].startswith('--pid='))]
```

— duplicate-argument collapsing, `--gapplication-service` (the D-Bus-activated GNOME app case, which
makes a command line useless as identity), `--pid=` stripping, and a whole Snap special case.

**What it cannot do.** X11 only, and its own README now points Wayland users at the extension.

**What `restore-wss` takes from it.** The warning list for command-line comparison, which is
directly reusable in the "is this app already running?" check that makes restore idempotent. And
confirmation that on Ubuntu, Snap-packaged applications need their own handling.

---

## 7. CRIU, and why not

[CRIU](https://criu.org) checkpoints a process tree to disk and restores it later, which would in
principle restore everything — open documents, unsaved buffers, scroll positions, the lot.

It is rejected, for reasons CRIU's own documentation gives:

* **A GUI client's state does not live only in the client.** "Dumping + restoring an application
  connected to a 'real' Xserver (e.g. on your laptop) is impossible now", because part of the state
  is in the server. On Wayland the same argument holds with more force: the client's window state
  lives in Mutter, and its Wayland socket connects to a process that would not be in the dump.
  Checkpointing the *compositor too* means checkpointing the whole graphical session including the
  GPU.
* **Device file descriptors cannot be checkpointed generically** — DRM/GPU access is the explicit
  example, and every accelerated GUI client has some.
* **Files passed over unix sockets** are on the "cannot be dumped" list; that is exactly what a
  Wayland client does with its buffers.

Beyond feasibility: a checkpoint is opaque, not user-editable, invalid after an application or
kernel upgrade, and enormous compared with a JSON file. The whole point of this idea's snapshot is
that it is small, readable, hand-editable and survives being restored onto a slightly different
machine. Not a checkpoint.

---

## 8. What `restore-wss` takes, in one table

| From | Taken |
| --- | --- |
| XSMP / `gnome-session` | The negative lesson: capture at shutdown is worthless against a crash. And the decomposition — restart command *plus* window placement. |
| `ksmserver` / KWin | User-facing choice of *previous / saved / empty* session; restore driven by a systemd user unit. |
| `xdg-session-management-v1` | Nothing usable on GNOME 46 — but a reserved `session_protocol` field per window so apps that restore themselves can be left alone on newer systems. |
| `smart-auto-move` | The window matcher: `wm_class` gate, character-histogram title distance, threshold **and** minimum spread, title-length penalty, settle-then-match timing, per-connector monitor-relative geometry. |
| Another Window Session Manager | The snapshot field list; launch by desktop file first, command line only as fallback; already-running check. |
| Window State Manager | A negative control on window identity (`get_id()` cannot cross a reboot). |
| `i3-resurrect` | `/proc` capture of `cmdline` and `cwd`; the terminal's cwd comes from its **child**, not from the emulator; per-app command mappings. |
| `i3-restore` | The `subprocesses` allow-list with per-program argument filtering, as the default command policy. |
| `tmux-resurrect` / `continuum` | Conservative default whitelist; per-program restore strategies; idempotency by registering what already exists; periodic saving as a crash-survival requirement. |
| `xsession-manager` | The catalogue of command-line comparison traps (`--gapplication-service`, `--pid=`, Snap). |
| CRIU | Rejected, with reasons. |

## 9. Conclusions that change the plan

1. **`PLAN.md`'s protocol section needs updating**: `xx_session_management_v1` is now
   `xdg-session-management-v1` in *staging*, Mutter has shipped it since GNOME 47 and KWin since
   April 2026. It is still irrelevant to this machine (GNOME 46, protocol absent from the registry)
   and still opt-in per application, so the design does not change — but the schema reserves a field
   for it, and `docs/limitations.md` will say which GNOME versions could eventually delegate.
2. **Nobody has built the whole thing.** The GNOME extensions restore windows and (in AWSM's case)
   applications; the i3 and tmux tools restore programs and working directories. Nothing continuous,
   crash-safe, document-aware *and* careful about commands exists. That is the gap.
3. **The command-replay safety model is the differentiator, and prior art supports it.** AWSM and
   `i3-resurrect` replay captured command lines through a shell with no review. `i3-restore` and
   `tmux-resurrect` — the two tools whose authors thought hardest about it — both landed on an
   allow-list. The answered open question in `PLAN.md` picks the same answer, and this study
   confirms it is the mainstream conclusion rather than excessive caution.
4. **The matcher is portable, and should be pure.** `smart-auto-move`'s algorithm is ~40 lines of
   arithmetic over `(wm_class, title)` pairs. Porting it into the Python daemon rather than the
   extension keeps the compositor-side component thin and makes the heuristic unit-testable, which
   is the only way its thresholds can be tuned honestly.
