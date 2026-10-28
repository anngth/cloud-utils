from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import IO, Literal


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
    input_file: str | Path | None = None,
    capture: bool | Literal["stderr"] = True,
) -> CommandResult:
    if input_text is not None and input_file is not None:
        raise ValueError("input_text and input_file are mutually exclusive")
    captured = capture is not False
    stdin_args: dict[str, object] = {}
    source: IO[bytes] | None = None
    if input_file is not None:
        # The child reads the file directly; the dump never enters memory.
        source = open(input_file, "rb")  # noqa: SIM115 - closed by the caller
        stdin_args["stdin"] = source
    elif input_text is not None:
        stdin_args["input"] = input_text
    elif captured:
        stdin_args["stdin"] = subprocess.DEVNULL
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=None if env is None else dict(env),
            stdout=subprocess.PIPE if capture is True else subprocess.DEVNULL if captured else None,
            stderr=subprocess.PIPE if captured else None,
            text=True,
            check=False,
            shell=False,
            **stdin_args,
        )
    finally:
        if source is not None:
            source.close()
    return CommandResult(
        completed.returncode,
        completed.stdout or "",
        completed.stderr or "",
    )
