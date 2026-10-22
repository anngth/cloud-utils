"""Child process helpers for dbt. Every command is an argv list."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from shared.process import CommandResult, run_process


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    stream: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> CommandResult:
    """Run one child. Capture by default; stream output live when asked."""
    if not stream:
        return run_process(argv, env=env, input_text=input_text)
    stdout_stream = stdout if stdout is not None else sys.stdout
    stderr_stream = stderr if stderr is not None else sys.stderr
    process = subprocess.Popen(
        list(argv),
        env=None if env is None else dict(env),
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=stdout_stream,
        stderr=stderr_stream,
        text=True,
        shell=False,
    )
    process.communicate(input_text)
    return CommandResult(process.returncode or 0)


def run_pipe(
    left: Sequence[str],
    right: Sequence[str],
    *,
    left_env: Mapping[str, str] | None = None,
    right_env: Mapping[str, str] | None = None,
    stream: bool = False,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> CommandResult:
    """Run `left | right`, wait for both children, first nonzero code wins."""
    if stream:
        return _run_pipe_live(left, right, left_env, right_env, stdout, stderr)
    return _run_pipe_captured(left, right, left_env, right_env)


def _run_pipe_captured(
    left: Sequence[str],
    right: Sequence[str],
    left_env: Mapping[str, str] | None,
    right_env: Mapping[str, str] | None,
) -> CommandResult:
    left_child = subprocess.Popen(
        list(left),
        env=None if left_env is None else dict(left_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    right_child = subprocess.Popen(
        list(right),
        env=None if right_env is None else dict(right_env),
        stdin=left_child.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    assert left_child.stdout is not None
    left_child.stdout.close()
    right_stdout, right_stderr = right_child.communicate()
    _left_stdout, left_stderr = left_child.communicate()
    return CommandResult(
        (left_child.returncode or 0) or (right_child.returncode or 0),
        right_stdout or "",
        (left_stderr or "") + (right_stderr or ""),
    )


def _run_pipe_live(
    left: Sequence[str],
    right: Sequence[str],
    left_env: Mapping[str, str] | None,
    right_env: Mapping[str, str] | None,
    stdout: TextIO | None,
    stderr: TextIO | None,
) -> CommandResult:
    stdout_stream = stdout if stdout is not None else sys.stdout
    stderr_stream = stderr if stderr is not None else sys.stderr
    left_child = subprocess.Popen(
        list(left),
        env=None if left_env is None else dict(left_env),
        stdout=subprocess.PIPE,
        stderr=stderr_stream,
        text=True,
        shell=False,
    )
    right_child = subprocess.Popen(
        list(right),
        env=None if right_env is None else dict(right_env),
        stdin=left_child.stdout,
        stdout=stdout_stream,
        stderr=stderr_stream,
        text=True,
        shell=False,
    )
    assert left_child.stdout is not None
    left_child.stdout.close()
    left_code = left_child.wait()
    right_code = right_child.wait()
    return CommandResult(left_code or right_code, "", "")
