"""Child process helpers for dbt. Every command is an argv list."""

from __future__ import annotations

import subprocess
import sys
import threading
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


def _close_pipe(stream: TextIO | None) -> None:
    """Close one parent-side pipe end; caller-owned streams never arrive."""
    if stream is not None and not stream.closed:
        stream.close()


def _kill_children(*children: subprocess.Popen) -> None:
    """Kill and reap children whose pipe unwound under an interrupt."""
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait()


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
    left_stderr = left_child.stderr
    assert left_child.stdout is not None and left_stderr is not None
    left_child.stdout.close()  # the right child owns the read end from here
    # Drain the left child's stderr while the right child runs: once the
    # OS pipe buffer fills, the left child blocks until this parent reads,
    # and a large dump would otherwise deadlock the whole pipe.
    drained: list[str] = []

    def _drain_left_stderr() -> None:
        try:
            drained.append(left_stderr.read())
        except (OSError, ValueError):  # pragma: no cover - fd closed first
            drained.append("")
        try:
            left_stderr.close()
        except (OSError, ValueError):  # pragma: no cover - already closed
            pass

    drain = threading.Thread(target=_drain_left_stderr, daemon=True)
    drain.start()
    try:
        right_stdout, right_stderr = right_child.communicate()
        drain.join()
        left_child.wait()
    except BaseException:
        # A bound interrupt handler raised SystemExit mid-communicate:
        # no child may outlive the pipe it was killed out of. The daemon
        # drain thread unblocks on its own once the left child is reaped.
        _kill_children(left_child, right_child)
        raise
    finally:
        # Belt and suspenders: no parent-side pipe end stays open.
        _close_pipe(right_child.stdout)
        _close_pipe(right_child.stderr)
    return CommandResult(
        (left_child.returncode or 0) or (right_child.returncode or 0),
        right_stdout or "",
        (drained[0] if drained else "") + (right_stderr or ""),
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
    try:
        left_code = left_child.wait()
        right_code = right_child.wait()
    except BaseException:
        # A bound interrupt handler raised SystemExit mid-wait: no child
        # may outlive the pipe. The stdout/stderr streams belong to the
        # caller and must not be closed here.
        _kill_children(left_child, right_child)
        raise
    return CommandResult(left_code or right_code, "", "")
