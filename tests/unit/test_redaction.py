from restore_wss.redaction import PLACEHOLDER, redact


def test_a_separate_password_argument_is_replaced():
    result = redact(["mysql", "-u", "root", "--password", "hunter2"])
    assert result.argv == ["mysql", "-u", "root", "--password", PLACEHOLDER]
    assert result.redacted == [4]


def test_an_inline_value_keeps_the_option_name():
    result = redact(["curl", "--token=abc123def456", "https://example.com"])
    assert result.argv[1] == f"--token={PLACEHOLDER}"
    assert "abc123def456" not in " ".join(result.argv)


def test_token_shaped_values_are_caught_wherever_they_are():
    result = redact(["gh", "auth", "login", "--with-token", "ghp_0123456789abcdefghij"])
    assert "ghp_0123456789abcdefghij" not in " ".join(result.argv)


def test_environment_assignments_with_secret_names_are_replaced():
    result = redact(["env", "AWS_SECRET_ACCESS_KEY=abcd/efgh", "aws", "s3", "ls"])
    assert result.argv[1] == f"AWS_SECRET_ACCESS_KEY={PLACEHOLDER}"
    assert result.argv[2:] == ["aws", "s3", "ls"]


def test_harmless_environment_assignments_are_left_alone():
    result = redact(["env", "EDITOR=vim", "git", "commit"])
    assert result.redacted == []
    assert result.argv[1] == "EDITOR=vim"


def test_the_ordinary_case_is_untouched():
    argv = ["ssh", "-o", "ProxyCommand=sleep 900", "my-host"]
    result = redact(argv)
    assert result.argv == argv
    assert result.redacted == []


def test_claude_and_friends_survive_intact():
    for argv in (["claude", "-r"], ["vim", "src/main.py"], ["htop"], ["git", "log", "--oneline"]):
        assert redact(argv).argv == argv


def test_a_trailing_option_with_no_value_does_not_crash():
    result = redact(["psql", "--password"])
    assert result.argv == ["psql", "--password"]


def test_a_caller_can_add_its_own_option_names():
    result = redact(["myapp", "--licence-key", "ABC-123"], extra_options=("licence-key",))
    assert result.argv[2] == PLACEHOLDER
