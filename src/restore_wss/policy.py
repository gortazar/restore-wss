"""Whether a captured command may be run again.

The sharpest edge in the whole idea. A stored ``cmdline`` is untrusted input captured without the
user thinking about it, and re-running it is executing a program on their behalf, at login, before
they have looked at the screen. Two tools that thought hard about this — ``i3-restore`` and
``tmux-resurrect`` — both landed on an allow-list (``docs/similar-tools.md`` §4, §5), and so does
this, per the answered open question in ``PLAN.md``.

Three modes, set in ``config.toml``:

``never``
    Reopen the terminal at the right working directory and nothing else.
``whitelist`` (the default)
    Re-run only commands whose *program* is on the allow-list below. Everything else is offered in
    the review step, where the user can run it once with their eyes open.
``always``
    Re-run everything the policy does not forbid outright.

Two rules override the mode, in both directions and always:

* **A command containing a redaction is never run automatically.** Part of it is missing by
  construction, so running it would be running something other than what was captured.
* **A command whose program is on the deny-list is never run automatically**, whatever the mode,
  because the cost of being wrong is not symmetric.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NEVER = "never"
WHITELIST = "whitelist"
ALWAYS = "always"
MODES = (NEVER, WHITELIST, ALWAYS)

#: Seeded with the obviously safe ones, per the answered open question: programs that connect,
#: display or edit, and whose worst case on a re-run is a wasted window.
DEFAULT_ALLOWED = (
    # remote sessions and agents
    "ssh",
    "mosh",
    "claude",
    "aider",
    "codex",
    # monitors and viewers
    "top",
    "htop",
    "btop",
    "btm",
    "watch",
    "journalctl",
    "less",
    "man",
    "tail",
    "glances",
    # editors and pagers
    "vi",
    "vim",
    "nvim",
    "helix",
    "hx",
    "emacs",
    "nano",
    "micro",
    # shells and multiplexers, which are where the user will carry on anyway
    "tmux",
    "screen",
    "zellij",
    # read-mostly development tools
    "lazygit",
    "tig",
    "gitui",
    "k9s",
    "ipython",
    "python3",
    "node",
    "irb",
)

#: Never re-run automatically, whatever the mode. These are the shapes of command whose accidental
#: repetition is destructive, irreversible, or a second copy of something expensive.
DEFAULT_DENIED = (
    "rm",
    "mv",
    "dd",
    "mkfs",
    "shred",
    "fdisk",
    "parted",
    "sudo",
    "su",
    "doas",
    "pkexec",
    "systemctl",
    "reboot",
    "shutdown",
    "poweroff",
    "apt",
    "apt-get",
    "dnf",
    "pacman",
    "snap",
    "flatpak",
    "docker",
    "podman",
    "kubectl",
    "terraform",
    "ansible-playbook",
    "make",
    "cargo",
    "npm",
    "pip",
    "curl",
    "wget",
    "git",  # `git push --force` is one history entry away from `git status`
)


@dataclass
class Decision:
    """What restore will do with one captured command, and why it says so."""

    run: bool
    reason: str
    #: True when the user could reasonably be asked about it in the review step. A denied or
    #: redacted command is offered too — with its reason — but never pre-ticked.
    offer: bool = True
    pre_ticked: bool = False


@dataclass
class CommandPolicy:
    mode: str = WHITELIST
    allowed: tuple[str, ...] = DEFAULT_ALLOWED
    denied: tuple[str, ...] = DEFAULT_DENIED
    #: Extra programs the user has added, kept separate so ``restore-wss`` can show which
    #: decisions come from its defaults and which from the config file.
    extra_allowed: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"unknown command policy {self.mode!r}; expected one of {MODES}")

    def allows(self, program: str) -> bool:
        return program in self.allowed or program in self.extra_allowed

    def decide(self, command: list[str], *, redacted: bool = False) -> Decision:
        if not command:
            return Decision(False, "the shell was at its prompt", offer=False)

        program = command[0].rsplit("/", 1)[-1]

        if redacted:
            return Decision(
                False,
                "part of this command was redacted at capture time, so it is incomplete",
            )
        if program in self.denied:
            return Decision(False, f"{program} is on the deny-list")
        if self.mode == NEVER:
            return Decision(False, "the command policy is 'never'")
        if self.mode == ALWAYS:
            return Decision(True, "the command policy is 'always'", pre_ticked=True)
        if self.allows(program):
            source = "the allow-list" if program in self.allowed else "your allow-list"
            return Decision(True, f"{program} is on {source}", pre_ticked=True)
        return Decision(False, f"{program} is not on the allow-list")
