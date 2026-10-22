"""Tests for secrets path resolution, bootstrap, and caching."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from db_tools.cli import run_cli
from db_tools.config import ConfigError, SecretsCache, resolve_secrets_file

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
    )
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
    )
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
    )
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
    )
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
    )
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
    )
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


def test_secrets_cache_reuses_object_until_file_or_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "secrets"
    path.write_text(EMPTY_SECRETS)
    cache = SecretsCache()
    first = cache.load(path)
    second = cache.load(path)
    assert first is second
    path.write_text(ONE_VPS_SECRETS)
    mtime = path.stat().st_mtime
    os.utime(path, (mtime + 5, mtime + 5))
    third = cache.load(path)
    assert third is not first
    assert third.vps == (VPS_ROW,)


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
