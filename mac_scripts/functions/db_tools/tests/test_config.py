"""Tests for secrets path resolution, first-run migration, and loading."""

from __future__ import annotations

import io
import stat
from pathlib import Path

import pytest

from db_tools.cli import run_cli
from db_tools.config import ConfigError, load_secrets, resolve_secrets_file
from db_tools.models import VpsTunnel

ICLOUD_BACKUPS = "Library/Mobile Documents/com~apple~CloudDocs/Backups"
ICLOUD_LEGACY = ICLOUD_BACKUPS + "/dbt_secrets"

EMPTY_SECRETS = "# nothing configured\n"
VPS_ROW = "vps|u|10.0.0.1|pg|5432|127.0.0.1|5432"
ONE_VPS_LEGACY = f'typeset -a VPS_CONFIGS=(\n    "{VPS_ROW}"\n)\n'
ONE_VPS = VpsTunnel("vps", "u", "10.0.0.1", "pg", 5432, "127.0.0.1", 5432)


def _resolve(tmp_path: Path, env: dict[str, str], *, example_text: str = EMPTY_SECRETS):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(exist_ok=True)
    example = tmp_path / "secrets.example.toml"
    example.write_text(example_text)
    return resolve_secrets_file(env, home=home, repo_root=repo_root, example=example)


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


def test_existing_destination_wins_over_every_legacy_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    destination = config_dir / "dbt" / "secrets.toml"
    destination.parent.mkdir(parents=True)
    destination.write_text("# current\n")
    (config_dir / "dbt" / "secrets").write_text(ONE_VPS_LEGACY)
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})
    assert resolved.path == destination
    assert resolved.notices == ()
    assert destination.read_text() == "# current\n"
    assert (config_dir / "dbt" / "secrets").exists()  # untouched


def test_config_dir_pointing_at_a_file_raises(tmp_path: Path) -> None:
    config_file = tmp_path / "config"
    config_file.write_text("")
    with pytest.raises(ConfigError):
        _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_file)})


# --- first run: migration from the zsh-array file ----------------------------------


def test_legacy_file_next_to_the_destination_is_converted_and_renamed(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    legacy = config_dir / "dbt" / "secrets"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(ONE_VPS_LEGACY)
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})

    assert resolved.path == config_dir / "dbt" / "secrets.toml"
    assert load_secrets(resolved.path).tunnels == (ONE_VPS,)
    assert not legacy.exists()
    assert (config_dir / "dbt" / "secrets.zsh.bak").read_text() == ONE_VPS_LEGACY
    (notice,) = resolved.notices
    assert notice.startswith(f"migrated secrets from {legacy} to {resolved.path}")
    assert "secrets.zsh.bak" in notice


def test_converted_file_is_private(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    (config_dir / "dbt").mkdir(parents=True)
    (config_dir / "dbt" / "secrets").write_text(ONE_VPS_LEGACY)
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})
    assert stat.S_IMODE(resolved.path.stat().st_mode) == 0o600


def test_icloud_legacy_is_converted_and_left_in_place(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ICLOUD_LEGACY).parent.mkdir(parents=True)
    (home / ICLOUD_LEGACY).write_text(ONE_VPS_LEGACY)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "dbt_secrets").write_text("typeset -a VPS_CONFIGS=(\n)\n")
    config_dir = tmp_path / "cfg"
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})

    assert resolved.path == config_dir / "dbt" / "secrets.toml"
    assert load_secrets(resolved.path).tunnels == (ONE_VPS,)  # iCloud wins over the repo
    assert (home / ICLOUD_LEGACY).read_text() == ONE_VPS_LEGACY
    (notice,) = resolved.notices
    assert notice == f"migrated secrets from {home / ICLOUD_LEGACY} to {resolved.path}"


def test_repo_legacy_is_converted_when_nothing_else_exists(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "dbt_secrets").write_text(ONE_VPS_LEGACY)
    config_dir = tmp_path / "cfg"
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})
    assert load_secrets(resolved.path).tunnels == (ONE_VPS,)
    assert (repo_root / "dbt_secrets").exists()


def test_invalid_legacy_rows_are_kept_as_comments_and_counted(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    (config_dir / "dbt").mkdir(parents=True)
    (config_dir / "dbt" / "secrets").write_text(
        'typeset -a VPS_CONFIGS=(\n  "bad|row"\n  "' + VPS_ROW + '"\n)\n'
        'typeset -a POSTGRES_TARGETS=(\n  "t|postgresql://u:p@h:5432/postgres|postgres"\n)\n'
    )
    resolved = _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})

    assert load_secrets(resolved.path).tunnels == (ONE_VPS,)
    text = resolved.path.read_text()
    assert "# not converted (invalid tunnels row): bad|row" in text
    assert "# not converted (invalid postgres.targets row): t|postgresql://u:p@h:5432" in text
    assert resolved.notices[1] == (
        f"2 invalid rows were not converted; see the comments in {resolved.path}"
    )


def test_unparseable_legacy_file_raises_and_writes_nothing(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    (config_dir / "dbt").mkdir(parents=True)
    legacy = config_dir / "dbt" / "secrets"
    legacy.write_text("typeset -a VPS_CONFIGS=(\n    echo hi\n)\n")
    with pytest.raises(ConfigError) as excinfo:
        _resolve(tmp_path, {"CLOUD_UTILS_CONFIG_DIR": str(config_dir)})
    assert f"cannot migrate {legacy}: line 2" in str(excinfo.value)
    assert not (config_dir / "dbt" / "secrets.toml").exists()
    assert legacy.exists()


# --- first run: nothing to migrate -------------------------------------------------


def test_example_is_copied_when_no_legacy_file_exists(tmp_path: Path) -> None:
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


def test_run_cli_migrates_the_old_file_and_reports_it(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    (config_dir / "dbt").mkdir(parents=True)
    (config_dir / "dbt" / "secrets").write_text(ONE_VPS_LEGACY)
    env = {"HOME": str(tmp_path / "home"), "CLOUD_UTILS_CONFIG_DIR": str(config_dir)}
    code, out, err = _run(["config"], env)
    assert code == 0
    assert "migrated secrets from" in err
    assert "1) vps: u@10.0.0.1  pg  :5432 -> 127.0.0.1:5432" in out


def test_run_cli_reports_a_bad_secrets_file_and_exits_one(tmp_path: Path) -> None:
    override = tmp_path / "secrets.toml"
    override.write_text('[[tunnels]]\nname = "x"\n')
    code, _out, err = _run(
        ["connect"], {"HOME": str(tmp_path / "home"), "DBT_SECRETS_FILE": str(override)}
    )
    assert code == 1
    assert "tunnels[1] (x): missing field 'user'" in err
