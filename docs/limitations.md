# Limitations

The honest list. **confirmed** means it was observed on the target machine (GNOME Shell 46,
Wayland, Ubuntu 24.04) with evidence in [platform-findings.md](platform-findings.md);
**by design** means it is a decision, not a defect.

This file is a deliverable, not an apology. The value of `restore-wss` depends on being clear about
which 80% works.

## Cannot be restored, ever

* **Unsaved work.** Nothing is saved on your behalf, at capture or at restore. A document with
  unsaved changes comes back as it was last written to disk, and applications with their own crash
  recovery (LibreOffice, editors) will do their own thing about that.
* **In-shell state.** A shell's history, environment, aliases, `sudo` timestamp, `ssh-agent`
  identities, partially typed lines, output already on screen: none of it survives the process
  dying. The ceiling is the working directory plus the foreground command, and that ceiling is
  reached — see below.
* **Anything behind a login.** A restored browser or mail client is a fresh process; whether it is
  still signed in is between it and its own state.
* **Scroll positions, cursor positions, selections, undo history.** These live inside the
  application. Nothing outside it can see them, and the standards-track answer for this
  (xdg-session-management-v1, plus the save/restore portal) is exactly the work that would fix it —
  see [similar-tools.md](similar-tools.md) §2 for where that stands.

## Recovered only approximately

* **Which window is which, after a reboot** — confirmed. There is no stable window identity, so
  matching a saved window to a live one is a heuristic: `wm_class`/app id as a hard gate, then a
  character-histogram distance over titles. Two windows of the same application with similar titles
  is the case it cannot resolve, and when the best and second-best candidates are within 60% of
  each other the match is **refused and reported** rather than guessed. A refused match means the
  window is left where it is, not moved somewhere wrong.
* **Which launch a new window belongs to** — confirmed. The activation token issued at launch
  reaches the application's environment but is not readable off the window
  (`get_startup_id()` is null on Shell 46), so a launched window is matched by application id and
  timing. Two launches of the same application in flight can be attributed to the wrong slot. Every
  match is labelled with the strategy that produced it.
* **Window size, when the application disagrees** — confirmed twice. `move_resize_frame()` is
  honoured for Wayland clients, exactly, including on a workspace nobody is looking at. What cannot
  be overridden is the application's own sizing: a terminal snaps to its character grid (700x500
  came back as 694x489 in the smoke test), and an app with a minimum size keeps it. When a size is
  refused, the accompanying move is dropped with it, so the window also keeps its old position.
* **The document, for applications without an adapter** — by design. Tier 0: the application is
  restored with nothing open rather than with a guess. [app-adapters.md](app-adapters.md) lists
  which applications have rules and how to add one.
* **The document, for D-Bus-activated applications** — confirmed. `nautilus
  --gapplication-service` has no document on its command line, so the answer comes from an open
  descriptor, the application's own history, or the recent store, in that order. Sometimes it will
  be the wrong document; every recovered document carries a confidence, and the review step marks
  the low ones.
* **Monitor identity across a replug** — partly confirmed. Layouts are keyed by connector name from
  `org.gnome.Mutter.DisplayConfig`, because Mutter's monitor indices renumber. Geometry is stored
  relative to the monitor, so a screen plugged in at a different offset still puts the window in
  the same corner. What is *not* verified is whether connector names are stable across real
  replugs on real hardware — the nested test session has one virtual monitor.
* **Workspace names and focus order** — by design, best effort. Names are captured and the active
  workspace is restored; the exact stacking and focus order are not reproduced beyond raising
  windows in the recorded order.

## Terminals and commands specifically

The terminal support is the most valuable part of this and also the most hedged.

* **What is captured** — confirmed: one entry per tab (each tab is a session leader on its own pty),
  the tab's exact working directory, and the foreground job's argv. A shell sitting at its prompt
  is captured as a shell at its prompt, with no command.
* **What is re-run** — by design, and it is a policy rather than a capability:

  | `commands.policy` | What happens |
  | --- | --- |
  | `never` | Every tab reopens at its working directory. No command is ever re-run. |
  | `whitelist` (default) | As above, plus the command is re-run when its program is on the allow-list — `ssh`, `claude`, editors, pagers, monitors. Everything else is shown with the reason it was not run. |
  | `always` | As above for everything except the deny-list and redacted commands. |

  Two rules override the mode in both directions: a **deny-listed** program (`rm`, `sudo`, `git`,
  package managers, `docker`, …) is never re-run automatically, and neither is a command containing
  a **redaction**, because part of it is missing by construction.
* **Secrets** — arguments that look like credentials (`--password`, `--token`, `FOO_SECRET=…`,
  GitHub/OpenAI/Slack/AWS/JWT-shaped values) are replaced **at capture time**, so they are never
  written to disk. This over-redacts on purpose: a false positive costs a command you retype, a
  false negative writes your password to a file.
* **A command is never handed to a shell.** It is captured as argv and re-run as argv. Pipes,
  redirections, `&&` and variable expansion arrive as literal arguments and do nothing.
* **Multi-tab restore depends on the emulator.** `gnome-terminal` and Konsole take several tab
  groups in one invocation, so a multi-tab window comes back as one window. Alacritty and kitty have
  no tab flag: only the first tab is restored, and the rest are dropped rather than opened as extra
  windows.
* **Which terminal window owns which tab is not known** — confirmed gap. `/proc` gives the tabs of
  a *server*, and one server serves every window of the application, so with two terminal windows
  open the tabs cannot yet be attributed to the right window. Both windows are restored; their tabs
  may be grouped wrongly.

## VPN

* **NetworkManager only** — by design, per the answered open question. `wg-quick`, Tailscale and
  bare `openvpn` processes are not detected.
* **A connection that needs a password or a 2FA code cannot be restored unattended** — by design.
  Activation is by UUID, so secrets already in the keyring work; anything else is reported as
  needing you, with a sentence saying so, rather than retried.
* **Only identity is stored** — the UUID, name and type. Never a credential.

## Structural, from the platform

* **X11 is not supported** — by decision. Wayland only.
* **The Shell cannot load an extension in place under Wayland** — confirmed. Installing or
  upgrading needs a log out and back in. The daemon can be restarted on its own, which is one
  reason all the state lives there.
* **Nothing is captured while the extension is not running.** The daemon says so rather than
  reporting an empty desktop.
* **Snap-confined applications cannot be tested in the nested session** — confirmed: snapd refuses
  to start a confined app from an arbitrary cgroup, which on this machine means LibreOffice,
  Firefox and Codium can only be verified by hand in a real session.
* **Single-instance and Electron applications** — expected. An application that hands a second
  launch to an existing process produces no new window, so restore cannot place it. These cap out
  at tier 0.
* **Flatpak sandboxing hides `/proc`** of the application, so anything derived from the process
  tree is unavailable for Flatpak apps.

## Deliberately not done

* **A snapshot history.** One snapshot and its previous generation, no more, per the answered open
  question: history is cheap to store and expensive in selection UX.
* **Restoring browser tabs.** Out of scope here by decision; the schema leaves room for a tier-2
  browser adapter.
* **True checkpoint/restore (CRIU).** Rejected with reasons in [similar-tools.md](similar-tools.md)
  §7.
* **Publishing the extension on extensions.gnome.org.** A separate daemon that spawns processes is
  not what that channel is for; the installer puts the extension in place instead.
