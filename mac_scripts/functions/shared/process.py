from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Literal, Mapping, Sequence

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
    capture: bool | Literal["stderr"] = True,
) -> CommandResult:
    captured = capture is not False
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        input=input_text,
        stdin=subprocess.DEVNULL if captured and input_text is None else None,
        stdout=subprocess.PIPE if capture is True else subprocess.DEVNULL if captured else None,
        stderr=subprocess.PIPE if captured else None,
        text=True,
        check=False,
        shell=False,
    )
    return CommandResult(
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
    )
