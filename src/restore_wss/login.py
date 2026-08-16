"""Deciding whether to offer a restore at login.

The answered open question in ``PLAN.md`` says this is configurable, and off by default: restoring
launches a dozen applications, which is a lot to do to somebody who just wanted to check their
email. So the autostart entry always runs and this decides — cheaply, and with a reason it can
state — whether anything should happen at all.

The test that makes the offer non-spurious is the **boot id**. A snapshot carries the boot id it
was captured under; if that is still the current one, the session was not lost to a reboot, the
user simply logged out and back in, and there is nothing to offer.
"""

from __future__ import annotations

from dataclasses import dataclass

from .capture import read_boot_id
from .config import Config
from .model import Snapshot


@dataclass
class LoginDecision:
    offer: bool
    reason: str
    #: True when the config says to go ahead without asking.
    unattended: bool = False


def decide(snapshot: Snapshot | None, config: Config, current_boot_id: str = "") -> LoginDecision:
    current_boot_id = current_boot_id or read_boot_id()

    if not config.restore_at_login:
        return LoginDecision(False, "restore.at_login is off in config.toml")
    if snapshot is None:
        return LoginDecision(False, "there is no snapshot to restore")
    if snapshot.is_empty:
        return LoginDecision(False, "the snapshot has no windows in it")
    if snapshot.boot_id and current_boot_id and snapshot.boot_id == current_boot_id:
        # Same boot: this is a log out and back in, not the reboot the snapshot survived.
        return LoginDecision(False, "the session was not lost to a reboot")
    return LoginDecision(
        True,
        f"the snapshot is from before this boot and has {len(snapshot.windows)} window(s)",
        unattended=config.restore_unattended,
    )
