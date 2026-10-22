"""dbt command line entry point."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from .config import ConfigError, SecretsCache, resolve_secrets_file
from .runtime import Runtime, default_runtime
from .secrets import SecretsError

USAGE = """\
usage: dbt <command> [args]

commands:
  help              show this help
  connect [filter]  open SSH tunnels for the secrets file
  disconnect [..]   close SSH tunnels started by dbt
  list [--status]   show secrets entries
  test [filter]     check the hosts used for tunnels
  sync <service>    sync Postgres or MongoDB databases
    sync list --status    test database connections
    sync -c [--dry-run]   delete backup databases
"""


def run_cli(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    stdout: TextIO,
    stderr: TextIO,
    dependencies: Runtime | None = None,
) -> int:
    del dependencies
    if not argv or argv[0] in ("help", "-h", "--help"):
        stdout.write(USAGE)
        return 0

    home_text = env.get("HOME", "")
    home = Path(home_text) if home_text else Path.home()
    try:
        secrets_path = resolve_secrets_file(
            env,
            home=home,
            repo_root=Path(__file__).resolve().parents[3],
            example=Path(__file__).resolve().parent / "secrets.example",
        )
        raw_secrets = SecretsCache().load(secrets_path)
    except (SecretsError, ConfigError) as exc:
        stderr.write(f"{exc}\n")
        return 1

    del raw_secrets
    stderr.write(f"unknown command: {argv[0]}\n" + USAGE)
    return 1
