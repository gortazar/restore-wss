from restore_wss.config import DEFAULT_CONFIG_TEXT, load_config, write_default_config
from restore_wss.policy import ALWAYS, WHITELIST


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_no_config_file_means_the_documented_defaults(tmp_path):
    config = load_config(tmp_path / "absent.toml")
    assert config.command_policy.mode == WHITELIST
    assert config.restore_at_login is False
    assert config.paused is False
    assert config.problems == ()


def test_the_shipped_default_file_parses_to_the_defaults(tmp_path):
    config = load_config(write(tmp_path, DEFAULT_CONFIG_TEXT))
    assert config.problems == ()
    assert config.command_policy.mode == WHITELIST
    assert config.restore_at_login is False


def test_settings_are_read(tmp_path):
    path = write(
        tmp_path,
        """
        [capture]
        paused = true
        exclude_apps = ["org.gnome.Nautilus"]
        exclude_paths = ["/home/user/private"]

        [commands]
        policy = "always"
        allow = ["deploy.sh"]
        deny = ["ssh"]

        [restore]
        at_login = true
        """,
    )
    config = load_config(path)
    assert config.paused
    assert config.excludes_app("org.gnome.Nautilus", "")
    assert config.excludes_path("/home/user/private/diary.odt")
    assert config.command_policy.mode == ALWAYS
    assert config.command_policy.decide(["deploy.sh"]).run
    # A user deny entry wins over the mode, like the built-in ones.
    assert not config.command_policy.decide(["ssh", "host"]).run
    assert config.restore_at_login


def test_a_broken_file_is_reported_not_raised(tmp_path):
    config = load_config(write(tmp_path, "this is not toml ["))
    assert config.problems
    assert config.command_policy.mode == WHITELIST


def test_an_unknown_policy_is_reported_and_falls_back(tmp_path):
    config = load_config(write(tmp_path, '[commands]\npolicy = "sometimes"\n'))
    assert any("sometimes" in problem for problem in config.problems)
    assert config.command_policy.mode == WHITELIST


def test_a_value_of_the_wrong_type_is_reported_not_fatal(tmp_path):
    config = load_config(write(tmp_path, "[capture]\nexclude_apps = 5\npaused = 'yes'\n"))
    assert len(config.problems) == 2
    assert config.exclude_apps == ()
    assert config.paused is False


def test_writing_the_default_file_does_not_clobber_an_existing_one(tmp_path):
    path = write(tmp_path, "[commands]\npolicy = 'never'\n")
    write_default_config(path)
    assert load_config(path).command_policy.mode == "never"


def test_the_default_file_is_written_when_absent(tmp_path):
    path = tmp_path / "sub" / "config.toml"
    write_default_config(path)
    assert path.exists()
    assert load_config(path).problems == ()
