import base64
import io
import json
from pathlib import Path
from typing import Callable, NoReturn

import pytest

from twofa.cli import TtyInputError, run_cli
from twofa.clipboard import ClipboardError
from twofa.ui import TwoFactorUi


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "twofa.jsonl"
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _load_records() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    ]


def _raise(error: Exception) -> NoReturn:
    raise error


def _run_case(name: str) -> dict[str, object]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    copied: str | None = None

    argv: list[str]
    read_secret: Callable[[str], str]
    clipboard_error: ClipboardError | None = None

    if name == "help":
        argv = ["--help"]
        read_secret = lambda _prompt: _raise(
            AssertionError("help must not read a secret")
        )
    elif name == "unknown-command":
        argv = ["unknown"]
        read_secret = lambda _prompt: _raise(
            AssertionError("unknown command must not read a secret")
        )
    elif name == "success":
        argv = []
        read_secret = lambda _prompt: SECRET
    elif name == "invalid-secret":
        argv = []
        read_secret = lambda _prompt: "!!!!"
    elif name == "clipboard-failure":
        argv = []
        read_secret = lambda _prompt: SECRET
        clipboard_error = ClipboardError("failed to copy code to clipboard")
    elif name == "missing-tty":
        argv = []
        read_secret = lambda _prompt: _raise(
            TtyInputError("interactive terminal required")
        )
    else:
        raise AssertionError(f"unknown parity case: {name}")

    def copy(code: str) -> None:
        nonlocal copied
        copied = code
        if clipboard_error is not None:
            raise clipboard_error

    exit_code = run_cli(
        argv,
        ui=TwoFactorUi(stdout, stderr),
        read_secret_fn=read_secret,
        copy_fn=copy,
        now=59,
    )
    stdout_bytes = stdout.getvalue().encode()
    stderr_bytes = stderr.getvalue().encode()

    return {
        "name": name,
        "stdout": base64.b64encode(stdout_bytes).decode("ascii"),
        "stderr": base64.b64encode(stderr_bytes).decode("ascii"),
        "exitCode": exit_code,
        "copied": copied,
        "secretLeaked": SECRET.encode() in stdout_bytes
        or SECRET.encode() in stderr_bytes,
    }


@pytest.mark.parametrize("expected", _load_records(), ids=lambda row: row["name"])
def test_python_matches_reviewed_javascript_record(
    expected: dict[str, object],
) -> None:
    assert _run_case(str(expected["name"])) == expected
