"""dbt command line entry point."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TextIO

from .runtime import Runtime, default_runtime

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
    del env, dependencies
    if not argv or argv[0] in ("help", "-h", "--help"):
        stdout.write(USAGE)
        return 0
    stderr.write(f"unknown command: {argv[0]}\n" + USAGE)
    return 1
