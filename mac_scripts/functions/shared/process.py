from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

def run_process(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    capture: bool = True,
) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        input=input_text,
        text=True,
        capture_output=capture,
        check=False,
        shell=False,
    )
    return CommandResult(
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
    )
