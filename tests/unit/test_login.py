from restore_wss.config import Config
from restore_wss.login import decide
from restore_wss.model import Snapshot, Window


def snapshot(boot_id="boot-old", windows=1):
    return Snapshot(
        boot_id=boot_id,
        windows=[Window(wm_class="Foo", app_id="foo.desktop") for _ in range(windows)],
    )


def test_nothing_is_offered_unless_the_user_asked_for_it():
    """Off by default, per the answered open question: a dozen applications is a lot to do to
    somebody who just wanted to check their email."""
    decision = decide(snapshot(), Config(), current_boot_id="boot-new")
    assert not decision.offer
    assert "at_login is off" in decision.reason


def test_a_reboot_is_what_makes_the_offer_worth_making():
    decision = decide(snapshot(), Config(restore_at_login=True), current_boot_id="boot-new")
    assert decision.offer
    assert "before this boot" in decision.reason


def test_logging_out_and_back_in_does_not_trigger_it():
    """The boot id is the whole reason the offer is not spurious."""
    decision = decide(
        snapshot(boot_id="boot-same"), Config(restore_at_login=True), current_boot_id="boot-same"
    )
    assert not decision.offer
    assert "not lost to a reboot" in decision.reason


def test_an_empty_or_missing_snapshot_offers_nothing():
    config = Config(restore_at_login=True)
    assert not decide(None, config, "boot-new").offer
    assert not decide(snapshot(windows=0), config, "boot-new").offer


def test_unattended_is_carried_through_only_when_configured():
    config = Config(restore_at_login=True, restore_unattended=True)
    assert decide(snapshot(), config, "boot-new").unattended
    assert not decide(snapshot(), Config(restore_at_login=True), "boot-new").unattended
