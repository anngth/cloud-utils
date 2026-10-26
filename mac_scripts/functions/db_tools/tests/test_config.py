"""Tests for secrets path resolution, first-run bootstrap, and loading."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from db_tools.cli import run_cli
from db_tools.config import ConfigError, load_secrets, resolve_secrets_file
from db_tools.models import VpsTunnel

ICLOUD_BACKUPS = "Library/Mobile Documents/com~apple~CloudDocs/Backups"

EMPTY_SECRETS = "# nothing configured\n"
ONE_VPS = VpsTunnel("vps", "u", "10.0.0.1", "pg", 5432, "127.0.0.1", 5432)


def _resolve(tmp_path: Path, env: dict[str, str], *, example_text: str = EMPTY_SECRETS):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    example = tmp_path / "secrets.example.toml"
    example.write_text(example_text)
    return resolve_secrets_file(env, home=home, example=example)


# --- where the file is ------------------------------------------------------------


def test_existing_override_is_used_without_bootstrap(tmp_path: Path) -> None:
    override = tmp_path / "override.toml"
    override.write_text(EMPTY_SECRETS)
    env = {"DBT_SECRETS_FILE": str(override), "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")}
    resolved = _resolve(tmp_path, env)
    assert resolved.path == override
    assert resolved.notices == ()
    assert not (tmp_path / "cfg").exists()


def test_missing_override_is_reported_and_the_default_is_used(tmp_path: Path) -> None:
    env = {"DBT_SECRETS_FILE": str(tmp_path / "gone")}
    resolved = _resolve(tmp_path, env)
    expected = tmp_path / "home" / ICLOUD_BACKUPS / "cloud-utils" / "dbt" / "secrets.toml"
    assert resolved.path == expected
    assert expected.read_text() == EMPTY_SECRETS
    assert any("DBT_SECRETS_FILE" in n and "gone" in n for n in resolved.notices)
    assert not (tmp_path / "gone").exists()


def test_existing_destination_is_kept_as_is(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    destination = config_dir / "dbt" / "secrets.toml"
    destination.parent.mkdir(parents=True)
    destination.write_text("# current\n")
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})
    assert resolved.path == destination
    assert resolved.notices == ()
    assert destination.read_text() == "# current\n"


def test_config_dir_pointing_at_a_file_raises(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text("")
    with pytest.raises(ConfigError):
        _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_file)})


# --- first run ---------------------------------------------------------------------


def test_example_is_copied_on_first_run(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    env = {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)}
    resolved = _resolve(tmp_path, env, example_text="# ex\n")
    assert resolved.path == config_dir / "dbt" / "secrets.toml"
    assert resolved.path.read_text() == "# ex\n"
    (notice,) = resolved.notices
    assert "created an empty secrets file" in notice
    assert str(resolved.path) in notice


def test_second_run_has_no_notice(tmp_path: Path) -> None:
    env = {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")}
    _resolve(tmp_path, env)
    assert _resolve(tmp_path, env).notices == ()


def test_shipped_example_is_a_valid_empty_secrets_file() -> None:
    example = Path(__file__).resolve().parents[1] / "secrets.example.toml"
    assert load_secrets(example).tunnels == ()


# --- loading ----------------------------------------------------------------------


def test_load_secrets_parses_the_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.toml"
    path.write_text(
        '[[tunnels]]\nname = "vps"\nuser = "u"\nhost = "10.0.0.1"\nservice = "pg"\n'
        'local_port = 5432\ntarget_host = "127.0.0.1"\ntarget_port = 5432\n'
    )
    assert load_secrets(path).tunnels == (ONE_VPS,)


def test_load_secrets_raises_config_error_when_path_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_secrets(tmp_path / "secrets.toml")
    assert str(tmp_path / "secrets.toml") in str(exc.value)


def test_load_secrets_raises_config_error_when_path_is_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_secrets(tmp_path)
    assert str(tmp_path) in str(exc.value)


def test_load_secrets_raises_config_error_when_file_is_unreadable(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text(EMPTY_SECRETS)
    secrets.chmod(0o000)
    try:
        with pytest.raises(ConfigError) as exc:
            load_secrets(secrets)
        assert str(secrets) in str(exc.value)
    finally:
        secrets.chmod(0o644)


# --- through the CLI --------------------------------------------------------------


def _run(argv: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_cli(argv, env=env, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_run_cli_prints_message_and_exits_one_when_secrets_vanish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "secrets.toml"
    override.write_text(EMPTY_SECRETS)

    def vanishing_load(path: Path) -> object:
        path.unlink()
        return load_secrets(path)

    monkeypatch.setattr("db_tools.cli.load_secrets", vanishing_load)
    code, _out, err = _run(
        ["connect"], {"HOME": str(tmp_path / "home"), "DBT_SECRETS_FILE": str(override)}
    )
    assert code == 1
    assert str(override) in err


def test_run_cli_prints_bootstrap_notice_on_stderr(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path / "home"), "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")}
    code, _out, err = _run(["list"], env)
    assert code == 0
    assert "created an empty secrets file" in err
    assert str(tmp_path / "cfg" / "dbt" / "secrets.toml") in err


def test_run_cli_reports_a_bad_secrets_file_and_exits_one(tmp_path: Path) -> None:
    override = tmp_path / "secrets.toml"
    override.write_text('[[tunnels]]\nname = "x"\n')
    code, _out, err = _run(
        ["connect"], {"HOME": str(tmp_path / "home"), "DBT_SECRETS_FILE": str(override)}
    )
    assert code == 1
    assert "tunnels[1] (x): missing field 'user'" in err
