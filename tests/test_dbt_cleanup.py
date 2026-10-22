"""dbt must treat secrets and backup names as data when it drops databases."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DBT = ROOT / "mac_scripts" / "dbt"
DB_TOOLS = ROOT / "mac_scripts" / "functions" / "db-tools"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_zsh(tmp_path: Path, script: str, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["CLOUD_UTILS_CONFIG_DIR"] = str(tmp_path / "config")
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        ["zsh", "-f", "-c", script],
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )


def test_postgres_url_password_is_data_not_shell(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    password = f"x'; touch {sentinel}; echo 'x"
    url = f"postgresql://user:{password}@127.0.0.1:5432/app"
    result = _run_zsh(
        tmp_path,
        f"""
source {_quote(str(DB_TOOLS / "postgres.zsh"))}
_parse_postgres_url {_quote(url)} tgt
print -r -- "$tgt_pass"
""",
    )

    assert not sentinel.exists()
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{password}\n"


def test_vps_config_field_is_data_not_shell(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    user = f"admin'; touch {sentinel}; echo '"
    config = f"vps|{user}|10.0.0.1|postgres|5432|127.0.0.1|5432"
    result = _run_zsh(
        tmp_path,
        f"""
source {_quote(str(DB_TOOLS / "connection.zsh"))}
parse_config {_quote(config)} vps_name vps_user vps_ip svc local_port target_host target_port
print -r -- "$vps_user"
""",
    )

    assert not sentinel.exists()
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{user}\n"


def test_mongo_exists_check_keeps_database_name_out_of_the_javascript(tmp_path: Path) -> None:
    name = "a'); db.getSiblingDB('admin').dropDatabase(); //"
    log = tmp_path / "mongo.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "mongosh",
        f"""#!/bin/bash
printf 'DB=%s\\n' "$DBT_MONGO_DB" >> {_quote(str(log))}
printf 'ARGS=%s\\n' "$*" >> {_quote(str(log))}
printf '%s\\n' true
exit 0
""",
    )
    result = _run_zsh(
        tmp_path,
        f"""
source {_quote(str(DB_TOOLS / "mongo.zsh"))}
_mongo_database_exists {_quote("mongodb://127.0.0.1:27017")} {_quote(name)}
""",
        path_prefix=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    assert f"DB={name}" in recorded
    args = next(line.removeprefix("ARGS=") for line in recorded if line.startswith("ARGS="))
    assert "process.env.DBT_MONGO_DB" in args
    assert "admin" not in args


def test_mongo_drop_keeps_database_name_out_of_the_javascript(tmp_path: Path) -> None:
    name = "a'); db.getSiblingDB('admin').dropDatabase(); //"
    log = tmp_path / "mongo.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "mongosh",
        f"""#!/bin/bash
printf 'DB=%s\\n' "$DBT_MONGO_DB" >> {_quote(str(log))}
printf 'ARGS=%s\\n' "$*" >> {_quote(str(log))}
exit 0
""",
    )
    result = _run_zsh(
        tmp_path,
        f"""
source {_quote(str(DB_TOOLS / "mongo.zsh"))}
_mongo_drop_database {_quote("mongodb://127.0.0.1:27017")} {_quote(name)}
""",
        path_prefix=bin_dir,
    )

    assert result.returncode == 0, result.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    assert f"DB={name}" in recorded
    args = next(line.removeprefix("ARGS=") for line in recorded if line.startswith("ARGS="))
    assert "process.env.DBT_MONGO_DB" in args
    assert "admin" not in args


def _client_bin(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql_log = tmp_path / "psql.log"
    mongo_log = tmp_path / "mongo.log"
    _write_executable(
        bin_dir / "psql",
        f"""#!/bin/bash
prev=""
sql=""
for arg in "$@"; do
  if [[ "$prev" == "-c" ]]; then
    printf '%s\\n' "$arg" >> {_quote(str(psql_log))}
    sql="$arg"
  fi
  prev="$arg"
done
if [[ "$sql" == SELECT\\ datname* ]]; then
  printf '%s\\n' 'weird"name_backup_20260101_120000'
fi
exit 0
""",
    )
    for name in ("pg_dump", "pg_restore"):
        _write_executable(
            bin_dir / name,
            """#!/bin/bash
if [[ "$1" == "--version" ]]; then
  printf '%s\\n' 'pg_dump (PostgreSQL) 16.1'
fi
exit 0
""",
        )
    _write_executable(
        bin_dir / "mongosh",
        f"""#!/bin/bash
printf 'DB=%s\\n' "$DBT_MONGO_DB" >> {_quote(str(mongo_log))}
printf 'ARGS=%s\\n' "$*" >> {_quote(str(mongo_log))}
if [[ "$*" == *getDBNames* ]]; then
  printf '%s\\n' "app_backup_20260101_120000"
fi
exit 0
""",
    )
    return bin_dir, psql_log, mongo_log


def _secrets(tmp_path: Path) -> Path:
    secrets = tmp_path / "secrets"
    secrets.write_text(
        """
typeset -a VPS_CONFIGS=()
typeset -a POSTGRES_SOURCES=(
  'src|postgresql://user:pass@127.0.0.1:5432/app|app'
)
typeset -a POSTGRES_TARGETS=(
  'dst|postgresql://user:pass@127.0.0.1:5432/app'
)
typeset -a MONGO_SOURCES=()
typeset -a MONGO_TARGETS=(
  'mdst|mongodb://user:pass@127.0.0.1:27017'
)
""",
        encoding="utf-8",
    )
    return secrets


def _dbt(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bin_dir, psql_log, mongo_log = _client_bin(tmp_path)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["CLOUD_UTILS_CONFIG_DIR"] = str(tmp_path / "config")
    env["DBT_SECRETS_FILE"] = str(_secrets(tmp_path))
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        ["zsh", "-f", str(DBT), *args],
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        check=False,
    )
    return result, psql_log, mongo_log


def test_invalid_postgres_index_does_not_call_psql(tmp_path: Path) -> None:
    result, psql_log, _mongo_log = _dbt(tmp_path, "sync", "postgres", "-s", "2", "-d", "1")

    assert result.returncode != 0
    assert "Invalid PostgreSQL source index" in result.stdout
    assert not psql_log.exists()


def test_dry_run_lists_postgres_backups_without_dropping_them(tmp_path: Path) -> None:
    result, psql_log, mongo_log = _dbt(tmp_path, "sync", "postgres", "-c", "-n")

    assert result.returncode == 0, result.stderr + result.stdout
    sql = psql_log.read_text(encoding="utf-8")
    assert "SELECT datname" in sql
    assert "DROP DATABASE" not in sql
    assert "weird\"name_backup_20260101_120000" in result.stdout
    if mongo_log.exists():
        assert "dropDatabase" not in mongo_log.read_text(encoding="utf-8")


def test_postgres_cleanup_quotes_backup_names_in_drop_statements(tmp_path: Path) -> None:
    result, psql_log, _mongo_log = _dbt(tmp_path, "sync", "postgres", "-c")

    assert result.returncode == 0, result.stderr + result.stdout
    sql = psql_log.read_text(encoding="utf-8").splitlines()
    assert 'DROP DATABASE IF EXISTS "weird""name_backup_20260101_120000";' in sql
