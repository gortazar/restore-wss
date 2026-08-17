"""``~/.restore-wss/config.toml`` — the user's settings.

Hand-editable on purpose, and read leniently: a config file with an unknown key, a misspelled
section or a value of the wrong type must not stop the daemon from capturing. Problems are
collected and reported (``restore-wss status`` prints them) rather than raised, because the
alternative is a machine that silently stopped snapshotting because of a typo.

Every default here is also the answer to a question in ``PLAN.md``: the command policy is
``whitelist``, restore at login is off until the user turns it on, and nothing is excluded until
they say so.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .policy import DEFAULT_ALLOWED, DEFAULT_DENIED, MODES, WHITELIST, CommandPolicy
from .storage import STATE_DIR_ENV, default_state_dir
from .terminals import DEFAULT_TERMINAL_WM_CLASSES

CONFIG_NAME = "config.toml"

DEFAULT_CONFIG_TEXT = """\
# restore-wss configuration. Everything here is optional; the defaults are the values shown.

[capture]
# Stop capturing without stopping the daemon.
paused = false
# Applications never recorded, by wm_class or desktop id.
exclude_apps = []
# Documents and working directories never recorded, as path prefixes.
exclude_paths = []
# Which applications are terminal emulators. Only these have their process tree walked.
# terminals = ["gnome-terminal-server", "Alacritty", "kitty"]

[commands]
# never | whitelist | always. See docs/limitations.md for what each one means.
policy = "whitelist"
# Programs added to the built-in allow-list.
allow = []
# Programs never re-run automatically, whatever the policy.
deny = []
# Extra option names whose values are redacted at capture time (--password and friends are
# already covered).
redact_options = []

[browsers]
# Capture tabs at all. Turning this off leaves the rest of restore-wss working.
enabled = true
# What to store per tab: "urls" (url + title), "titles" (titles only), or "none" (shape only).
store = "urls"
# Tabs whose URL matches any of these substrings or glob patterns are never recorded.
exclude_urls = []
# Which applications are browsers. Only these have a browser block attached.
# browsers = ["firefox"]

[restore]
# Offer to restore at the first login after a reboot. Off by default: restoring launches a dozen
# applications, which is a lot to do to someone who just wanted to check their email.
at_login = false
# Restore without asking. Only meaningful together with at_login.
unattended = false
"""


@dataclass
class Config:
    paused: bool = False
    exclude_apps: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    terminals: tuple[str, ...] = DEFAULT_TERMINAL_WM_CLASSES
    command_policy: CommandPolicy = field(default_factory=CommandPolicy)
    redact_options: tuple[str, ...] = ()
    restore_at_login: bool = False
    restore_unattended: bool = False
    browsers_enabled: bool = True
    #: ``urls`` | ``titles`` | ``none`` — how much of a tab is written down.
    browser_store: str = "urls"
    exclude_urls: tuple[str, ...] = ()
    browsers: tuple[str, ...] = ()
    #: Everything wrong with the file, in the user's words rather than a traceback.
    problems: tuple[str, ...] = ()

    def excludes_app(self, wm_class: str, app_id: str) -> bool:
        return any(name in (wm_class, app_id) for name in self.exclude_apps)

    def excludes_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.exclude_paths)

    def excludes_url(self, url: str) -> bool:
        """Whether a tab's URL is one the user asked never to record.

        Substring *or* glob, because "the bank" and "https://*.example.com/*" are both things people
        mean, and asking them to know which is which is a bad trade for one line of code.
        """
        from fnmatch import fnmatch

        return any(
            pattern in url or fnmatch(url, pattern) for pattern in self.exclude_urls if pattern
        )


def default_config_path() -> Path:
    """``~/.restore-wss/config.toml``, or under ``RESTORE_WSS_HOME`` when that is set."""
    import os

    root = os.environ.get(STATE_DIR_ENV)
    base = Path(root) if root else default_state_dir().parent
    return base / CONFIG_NAME


def _strings(value, problems: list[str], where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{where} should be a list of strings; ignoring it")
        return ()
    return tuple(value)


def _bool(value, problems: list[str], where: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        problems.append(f"{where} should be true or false; using {str(default).lower()}")
        return default
    return value


def load_config(path: Path | None = None) -> Config:
    """Read the config file. A missing file is not a problem; a broken one is a reported one."""
    path = path or default_config_path()
    problems: list[str] = []
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        return Config(problems=(f"{path}: {error}; using defaults",))

    capture = raw.get("capture") or {}
    commands = raw.get("commands") or {}
    restore = raw.get("restore") or {}
    browsers = raw.get("browsers") or {}

    store = browsers.get("store", "urls")
    if store not in ("urls", "titles", "none"):
        problems.append(
            f"browsers.store = {store!r} is not one of ('urls', 'titles', 'none'); using 'urls'"
        )
        store = "urls"

    mode = commands.get("policy", WHITELIST)
    if mode not in MODES:
        problems.append(f"commands.policy = {mode!r} is not one of {MODES}; using {WHITELIST!r}")
        mode = WHITELIST

    terminals = _strings(capture.get("terminals"), problems, "capture.terminals")
    denied = DEFAULT_DENIED + _strings(commands.get("deny"), problems, "commands.deny")

    return Config(
        paused=_bool(capture.get("paused"), problems, "capture.paused", False),
        exclude_apps=_strings(capture.get("exclude_apps"), problems, "capture.exclude_apps"),
        exclude_paths=_strings(capture.get("exclude_paths"), problems, "capture.exclude_paths"),
        terminals=terminals or DEFAULT_TERMINAL_WM_CLASSES,
        command_policy=CommandPolicy(
            mode=mode,
            allowed=DEFAULT_ALLOWED,
            denied=denied,
            extra_allowed=_strings(commands.get("allow"), problems, "commands.allow"),
        ),
        redact_options=_strings(
            commands.get("redact_options"), problems, "commands.redact_options"
        ),
        browsers_enabled=_bool(browsers.get("enabled"), problems, "browsers.enabled", True),
        browser_store=store,
        exclude_urls=_strings(browsers.get("exclude_urls"), problems, "browsers.exclude_urls"),
        browsers=_strings(browsers.get("browsers"), problems, "browsers.browsers"),
        restore_at_login=_bool(restore.get("at_login"), problems, "restore.at_login", False),
        restore_unattended=_bool(restore.get("unattended"), problems, "restore.unattended", False),
        problems=tuple(problems),
    )


def write_default_config(path: Path | None = None) -> Path:
    """Write the commented default file, if there is not one already."""
    path = path or default_config_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(DEFAULT_CONFIG_TEXT)
    return path
