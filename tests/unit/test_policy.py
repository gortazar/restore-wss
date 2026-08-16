import pytest

from restore_wss.policy import ALWAYS, NEVER, WHITELIST, CommandPolicy


def test_the_default_is_the_whitelist():
    assert CommandPolicy().mode == WHITELIST


def test_a_whitelisted_program_is_re_run():
    decision = CommandPolicy().decide(["ssh", "my-host"])
    assert decision.run
    assert decision.pre_ticked
    assert "allow-list" in decision.reason


def test_the_scenario_from_the_plan_is_allowed():
    assert CommandPolicy().decide(["claude", "-r"]).run


def test_an_unknown_program_is_offered_but_not_run():
    decision = CommandPolicy().decide(["./deploy.sh", "production"])
    assert not decision.run
    assert decision.offer
    assert not decision.pre_ticked
    assert "not on the allow-list" in decision.reason


def test_a_dangerous_program_is_never_run_even_in_always_mode():
    """rm -rf-shaped history must not run because a mode was set to 'always'."""
    decision = CommandPolicy(mode=ALWAYS).decide(["rm", "-rf", "build"])
    assert not decision.run
    assert "deny-list" in decision.reason


def test_a_redacted_command_is_never_run_automatically():
    for mode in (WHITELIST, ALWAYS):
        decision = CommandPolicy(mode=mode).decide(
            ["ssh", "--password", "<redacted>"], redacted=True
        )
        assert not decision.run
        assert "redacted" in decision.reason


def test_never_mode_runs_nothing():
    assert not CommandPolicy(mode=NEVER).decide(["ssh", "my-host"]).run


def test_always_mode_runs_what_is_neither_denied_nor_redacted():
    assert CommandPolicy(mode=ALWAYS).decide(["./deploy.sh"]).run


def test_a_shell_at_its_prompt_is_not_something_to_ask_about():
    decision = CommandPolicy().decide([])
    assert not decision.run
    assert not decision.offer


def test_the_program_is_matched_by_basename():
    assert CommandPolicy().decide(["/usr/bin/htop"]).run


def test_the_user_can_extend_the_allow_list():
    policy = CommandPolicy(extra_allowed=("deploy.sh",))
    decision = policy.decide(["deploy.sh"])
    assert decision.run
    assert "your allow-list" in decision.reason


def test_an_unknown_mode_is_rejected_loudly():
    with pytest.raises(ValueError, match="unknown command policy"):
        CommandPolicy(mode="sometimes")
