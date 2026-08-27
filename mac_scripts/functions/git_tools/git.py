from pathlib import Path
from typing import Mapping, Sequence

from shared.process import CommandResult, run_process

def run_git(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    runner=run_process,
) -> CommandResult:
    return runner(["git", *args], cwd=cwd, env=env, capture=True)
