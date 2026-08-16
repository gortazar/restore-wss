# What this platform actually exposes

Measured on the target machine, not remembered:

```
GNOME Shell 46.0 · Mutter 14 · gjs 1.80.2 · Wayland · Ubuntu 24.04
```

Everything here is reproducible with a committed tool, and the raw output of each is committed
next to it. Findings that were *not* established are in [Still unknown](#still-unknown) rather
than guessed.

## 1. The compositor advertises no session-management protocol

```console
$ tools/wayland-globals.sh
… xdg_activation_v1 xdg_wm_base zwp_idle_inhibit_manager_v1 …
```

Full output: [`probe-data/wayland-globals.txt`](probe-data/wayland-globals.txt). There is no
`xdg_session_manager_v1` and no `xx_session_manager_v1`; Mutter has implemented the protocol only
since the `gnome-47` branch (see [similar-tools.md §2](similar-tools.md)). `xdg_activation_v1`
*is* present, which is what issues activation tokens at launch.

**Consequence.** Everything this project does has to be built from introspection. The schema keeps
a `session_protocol` field per window so that on GNOME 47+ a window whose application restores
itself can be recorded and skipped, but no code path here depends on the protocol existing.

## 2. Window properties, workspaces, monitors and placement

These were established for this exact Shell version by the sibling `gnome-tasks` project, whose
`docs/gnome-internals.md` carries the probe traces. Rather than re-run the same probe, the findings
this project depends on are restated here, with the ones that shape `restore-wss` marked:

| Finding | Consequence here |
| --- | --- |
| `Meta.Window` exposes `get_wm_class()`, `get_title()`, `get_pid()`, `get_gtk_application_id()`, `get_gtk_window_object_path()`, `get_sandboxed_app_id()`, `get_maximized()`, `get_frame_rect()` | The capture record in `docs/state-schema.md` |
| `get_id()` / `get_stable_sequence()` are per-session | **Window identity across a reboot must be heuristic** — hence the ported `smart-auto-move` matcher |
| At `window-created` the window has no `wm_class` and no app; `Shell.WindowTracker` returns a synthetic `window:N` id | Capture must be signal-driven and must ignore `window:N` |
| Geometry is `0x0` until the client commits a buffer (52–1325 ms after creation, app-dependent) | A zero rect means "not known yet", never "at the origin" |
| Monitor connector names come from `org.gnome.Mutter.DisplayConfig.GetCurrentState`, not from `Meta` | Monitors are keyed by connector + EDID over D-Bus |
| `move_resize_frame()` from an extension **is** honoured for Wayland clients, including on an inactive workspace; the app's own minimum size wins, and a refused size drops the accompanying move | M3 placement is viable; `docs/limitations.md` records the caveat |
| The activation token reaches the application's environment but `Meta.Window.get_startup_id()` is `null` | Launch→window matching is by app id and timing, and must label itself a guess |

## 3. Terminals: what a multi-tab terminal looks like from outside

The restore-wss-specific unknown, and the most valuable feature in the plan. Probed with
[`tools/proc-probe.py`](../tools/proc-probe.py) against a real `gnome-terminal` in a nested headless
Shell; the trace is committed as
[`tests/fixtures/proc/gnome-terminal-two-tabs.json`](../tests/fixtures/proc/gnome-terminal-two-tabs.json).

```
gnome-terminal-  pid 177551  pgrp 176842  tpgid -1   sid 176842  tty 0      cwd /home/user
  bash           pid 177787  pgrp 177787  tpgid 177787  sid 177787  tty 34823  cwd /home/user
      cmd: bash -c "ssh -o ProxyCommand='sleep 900' my-host; true"
    ssh          pid 177790  pgrp 177787  tpgid 177787  sid 177787  tty 34823
      cmd: ssh -o ProxyCommand=sleep 900 my-host
      sleep      pid 177792  …
  bash           pid 177788  pgrp 177788  tpgid 177788  sid 177788  tty 34824  cwd /home/user/.cache/restore-wss-probe/my-repo
      cmd: bash -c 'sleep 900; true'
    sleep        pid 177791  …
```

Four findings, all of which the design depends on:

1. **One tab = one direct child of `gnome-terminal-server`, in its own session and on its own
   pty.** The tabs above are `sid 177787 / tty 34823` and `sid 177788 / tty 34824`. So tabs are
   *enumerable from `/proc` alone*, with no cooperation from the terminal: group the server's
   descendants by session id. Multi-tab capture is therefore feasible, which was an open risk in
   `PLAN.md`.
2. **The server's own `cwd` is useless** (`/home/user`), exactly as `i3-resurrect` assumes. The
   working directory that matters is the per-tab shell's, and it is exact — no title parsing needed.
   This is *better* than the ceiling `gnome-tasks` recorded (title-derived cwd), because that project
   deliberately never walked the process tree.
3. **The window's PID is the server's PID**, and one server serves every window and tab of the app.
   A terminal window cannot be matched to its tabs by PID; the link has to come from the window ↔
   tab correspondence, which `/proc` does not provide (see [Still unknown](#still-unknown)).
4. **`comm` in `/proc/<pid>/stat` is truncated to 15 characters** — `gnome-terminal-server` is
   stored as `gnome-terminal-`. Searching for a process by name without accounting for that finds
   nothing, silently.

### Which process is "the" foreground one

Answered exactly, with the kernel's own answer rather than a heuristic:
[`tests/fixtures/proc/interactive-shell-foreground-job.json`](../tests/fixtures/proc/interactive-shell-foreground-job.json),
an interactive `bash -i` with `claude -r` running in it — the scenario `PLAN.md` opens with:

```
bash    pid 182393  pgrp 182393  tpgid 182461  sid 182393  is_foreground false
  claude -r  pid 182461  pgrp 182461  tpgid 182461  sid 182393  is_foreground true
  cwd /home/user/.cache/restore-wss-probe/my-repo
```

**The rule: the foreground job of a tab is the descendant whose process group equals the session
leader's `tpgid`.** The shell itself is *not* it when a job is running, and this is a fact from the
tty layer, not an inference from timing or CPU use. If `tpgid` equals the shell's own pgrp, the
shell is at its prompt and there is nothing to re-run — which is the correct capture for "a terminal
sitting in `~/git/my-repo`".

An important detail visible above: `claude`'s `exe` resolves to a versioned path
(`~/.local/share/claude/versions/2.1.233`) while its `cmdline[0]` is `claude`. The **command policy
must key on `cmdline[0]`'s basename**, not on `exe`, or every version bump silently drops a program
off the allow-list.

## 4. Documents: which tier-1 sources actually pay off

| Source | Verdict here | Evidence |
| --- | --- | --- |
| `/proc/<pid>/cmdline` | Works when the app was launched with the document, useless for D-Bus-activated apps (`--gapplication-service`) | `gnome-tasks` probe; `xsession-manager` strips exactly that argument when comparing |
| `/proc/<pid>/cwd` | Exact for terminal *tabs*, meaningless for GUI apps (inherits the launcher's) | §3 above |
| `/proc/<pid>/fd` | Sometimes: Nautilus holds an fd on the directory it shows; a text editor holds none | `gnome-tasks` probe |
| `recently-used.xbel` | **Real and usable.** 1000 entries (the cap) on this machine, each with per-application `exec` and `modified` timestamps. Recorded by 25 distinct applications here, including Nautilus, Image Viewer, Evince, gedit, portals and `snap.firefox` | read on 2026-08-16; `gnome-tasks` had listed this as "not established" |
| LibreOffice's own history | **Not in `recently-used.xbel`.** LibreOffice keeps its own list — 25 `HistoryItem`s under `Histories`/`PickList` in `registrymodifications.xcu` (here: `~/snap/libreoffice/377/.config/libreoffice/4/user/`) | grepped on 2026-08-16 |

So the tier-1 ladder in `PLAN.md` survives contact, with one correction: **the flagship
"Thesis in LibreOffice" case needs a per-app adapter reading LibreOffice's own picklist**, because
the freedesktop recent store does not see it.

## 5. The nested test session, and what it cannot host

[`tools/nested-shell.sh`](../tools/nested-shell.sh) (from `gnome-tasks`, unchanged in substance)
boots a real headless GNOME Shell 46 on a private bus. Confirmed working here for
`gnome-terminal`.

**Snap-packaged applications still cannot be launched into it**, which matters because LibreOffice,
Firefox and Codium are all snaps on this machine:

```
$ libreoffice --writer ~/Thesis.txt          # inside the nested session
/user.slice/…/vte-spawn-….scope is not a snap cgroup for tag snap.libreoffice.libreoffice
```

snapd refuses to start a confined app from an arbitrary cgroup. The Wayland-socket half of this
problem is already handled (the nested display is called `wayland-9`, which snapd's apparmor profile
permits), but the cgroup half is not solvable from inside the harness. **Consequence:** LibreOffice
and Codium capture/restore must be verified by hand in the real session, and that verification is
recorded in `STATUS.md` rather than automated.

## Still unknown

* **Which terminal window owns which tab.** `/proc` gives tabs; `Meta.Window` gives windows; nothing
  observed so far links them. Candidates to try before promising per-tab restore: the VTE
  `WINDOWID`/`vte-spawn-<uuid>` systemd scope name visible in the tab's cgroup, and
  `gnome-terminal`'s own D-Bus interface. The scope name appears in the error message above, so the
  data exists — it has not yet been correlated with a window.
* **Whether a captured tab can be reopened with its cwd *and* command** in gnome-terminal
  specifically (`--tab --working-directory=… -- cmd`), and whether the emulator on this machine
  accepts several such tabs in one invocation.
* **Whether connector names from DisplayConfig are stable across a real replug** — one virtual
  monitor in the nested session cannot answer it.
* **LibreOffice and Codium window↔document correlation**, blocked by the snap issue above.
* **Snapshot cadence cost** (battery, SSD writes) — to be measured once the daemon writes for real.
