"""Tests for secrets path resolution, bootstrap, and loading."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from db_tools.cli import run_cli
from db_tools.config import ConfigError, load_secrets, resolve_secrets_file

ICLOUD_BACKUPS = "Library/Mobile Documents/com~apple~CloudDocs/Backups"
ICLOUD_LEGACY = ICLOUD_BACKUPS + "/dbt_secrets"

EMPTY_SECRETS = "typeset -a VPS_CONFIGS=(\n)\n"
VPS_ROW = "vps|u|10.0.0.1|pg|5432|127.0.0.1|5432"
ONE_VPS_SECRETS = f"typeset -a VPS_CONFIGS=(\n    \"{VPS_ROW}\"\n)\n"


def test_existing_override_is_used_without_bootstrap(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config_dir = tmp_path / "config"
    override = tmp_path / "override"
    override.write_text(EMPTY_SECRETS)
    path = resolve_secrets_file(
        {"DBT_SECRETS_FILE": str(override)},
        home=home,
        repo_root=tmp_path / "repo",
        example=tmp_path / "missing.example",
    ).path
    assert path == override
    assert not (config_dir / "dbt" / "secrets").exists()


def test_missing_override_is_ignored_for_default_destination(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    example = tmp_path / "example.secrets"
    example.write_text(EMPTY_SECRETS)
    env = {"DBT_SECRETS_FILE": str(tmp_path / "missing")}
    path = resolve_secrets_file(
        env,
        home=home,
        repo_root=tmp_path / "repo",
        example=example,
    ).path
    expected = home / ICLOUD_BACKUPS / "cloud-utils" / "dbt" / "secrets"
    assert path == expected
    assert expected.exists()
    assert expected.read_text() == EMPTY_SECRETS
    assert not (tmp_path / "missing").exists()


def test_existing_destination_wins_over_legacy_with_other_bytes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    legacy = home / ICLOUD_LEGACY
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy bytes")
    destination = home / ICLOUD_BACKUPS / "cloud-utils" / "dbt" / "secrets"
    destination.parent.mkdir(parents=True)
    destination.write_text("current bytes")
    example = tmp_path / "example.secrets"
    example.write_text("example bytes")
    path = resolve_secrets_file(
        {},
        home=home,
        repo_root=tmp_path / "repo",
        example=example,
    ).path
    assert path == destination
    assert path.read_text() == "current bytes"
    assert legacy.read_text() == "legacy bytes"


def test_icloud_legacy_is_copied_even_when_repo_legacy_exists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (home / ICLOUD_LEGACY).parent.mkdir(parents=True)
    (home / ICLOUD_LEGACY).write_text("icloud bytes")
    (repo_root / "dbt_secrets").write_text("repo bytes")
    config_dir = tmp_path / "config"
    env = {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)}
    path = resolve_secrets_file(
        env,
        home=home,
        repo_root=repo_root,
        example=tmp_path / "example.secrets",
    ).path
    assert path == config_dir / "dbt" / "secrets"
    assert path.read_text() == "icloud bytes"


def test_repo_legacy_is_copied_when_icloud_legacy_is_missing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "dbt_secrets").write_text("repo bytes")
    config_dir = tmp_path / "config"
    env = {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)}
    path = resolve_secrets_file(
        env,
        home=home,
        repo_root=repo_root,
        example=tmp_path / "example.secrets",
    ).path
    assert path == config_dir / "dbt" / "secrets"
    assert path.read_text() == "repo bytes"


def test_example_is_copied_when_no_legacy_file_exists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    example = tmp_path / "example.secrets"
    example.write_text("example bytes")
    config_dir = tmp_path / "config"
    env = {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)}
    path = resolve_secrets_file(
        env,
        home=home,
        repo_root=repo_root,
        example=example,
    ).path
    assert path == config_dir / "dbt" / "secrets"
    assert path.read_text() == "example bytes"


def test_config_dir_pointing_at_a_file_raises(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config_file = tmp_path / "config"
    config_file.write_text("")
    env = {"CLOUD_UTILS_CONFIG_DIR": str(config_file)}
    with pytest.raises(ConfigError):
        resolve_secrets_file(
            env,
            home=home,
            repo_root=tmp_path / "repo",
            example=tmp_path / "example.secrets",
        )


def test_load_secrets_parses_the_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets"
    path.write_text(ONE_VPS_SECRETS)
    assert load_secrets(path).vps == (VPS_ROW,)


def test_load_secrets_raises_config_error_when_path_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_secrets(tmp_path / "secrets")
    assert str(tmp_path / "secrets") in str(exc.value)


def test_load_secrets_raises_config_error_when_path_is_a_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError) as exc:
        load_secrets(tmp_path)
    assert str(tmp_path) in str(exc.value)


def test_load_secrets_raises_config_error_when_file_is_unreadable(
    tmp_path: Path,
) -> None:
    secrets = tmp_path / "secrets"
    secrets.write_text(EMPTY_SECRETS)
    secrets.chmod(0o000)
    try:
        with pytest.raises(ConfigError) as exc:
            load_secrets(secrets)
        assert str(secrets) in str(exc.value)
    finally:
        secrets.chmod(0o644)


def test_run_cli_prints_message_and_exits_one_when_secrets_vanish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "secrets"
    override.write_text(EMPTY_SECRETS)

    def vanishing_load(path: Path) -> object:
        path.unlink()
        return load_secrets(path)

    monkeypatch.setattr("db_tools.cli.load_secrets", vanishing_load)
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(
        ["connect"],
        env={"HOME": str(tmp_path / "home"), "DBT_SECRETS_FILE": str(override)},
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert str(override) in err.getvalue()


# --- bootstrap notices -----------------------------------------------------------


def _resolve(tmp_path: Path, env: dict[str, str]):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    example = tmp_path / "example.secrets"
    example.write_text(EMPTY_SECRETS)
    return resolve_secrets_file(
        env, home=home, repo_root=tmp_path / "repo", example=example
    )


def test_existing_override_has_no_notice(tmp_path: Path) -> None:
    override = tmp_path / "override"
    override.write_text(EMPTY_SECRETS)
    assert _resolve(tmp_path, {"DBT_SECRETS_FILE": str(override)}).notices == ()


def test_missing_override_is_reported(tmp_path: Path) -> None:
    resolved = _resolve(
        tmp_path,
        {
            "DBT_SECRETS_FILE": str(tmp_path / "gone"),
            "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg"),
        },
    )
    assert any("DBT_SECRETS_FILE" in n and "gone" in n for n in resolved.notices)


def test_bootstrap_from_example_says_the_file_is_empty(tmp_path: Path) -> None:
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")})
    (notice,) = resolved.notices
    assert "created an empty secrets file" in notice
    assert str(resolved.path) in notice


def test_bootstrap_from_legacy_says_migrated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "dbt_secrets").write_text(ONE_VPS_SECRETS)
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")})
    (notice,) = resolved.notices
    assert notice.startswith("migrated secrets from")
    assert str(repo / "dbt_secrets") in notice


def test_second_run_has_no_notice(tmp_path: Path) -> None:
    env = {"CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")}
    _resolve(tmp_path, env)
    assert _resolve(tmp_path, env).notices == ()


def test_run_cli_prints_bootstrap_notice_on_stderr(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = run_cli(
        ["list"],
        env={"HOME": str(tmp_path / "home"), "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "cfg")},
        stdout=out,
        stderr=err,
    )
    assert code == 0
    assert "created an empty secrets file" in err.getvalue()
    assert str(tmp_path / "cfg" / "dbt" / "secrets") in err.getvalue()


def test_run_cli_connect_reports_bad_second_line(tmp_path: Path) -> None:
    override = tmp_path / "secrets"
    override.write_text("typeset -a VPS_CONFIGS=(\n    echo hi\n)\n")
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(
        ["connect"],
        env={"HOME": str(tmp_path / "home"), "DBT_SECRETS_FILE": str(override)},
        stdout=out,
        stderr=err,
    )
    assert code == 1
    assert "line 2" in err.getvalue()
