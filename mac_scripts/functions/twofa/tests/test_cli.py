import builtins
from dataclasses import dataclass, field
import io
from typing import Callable

import pytest

import twofa.cli as cli
from twofa.cli import read_secret, run_cli
from twofa.ui import TwoFactorUi

HELP_GOLDEN = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Usage: 2fa [command]\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m│\x1b[39m  \x1b[32m2fa\x1b[39m  "
    "\x1b[90mPrompt for Base32 secret; copy 6-digit TOTP\x1b[39m\n"
    "\x1b[36m│\x1b[39m  \x1b[32m2fa (help | -h | --help)\x1b[39m  "
    "\x1b[90mShow this help\x1b[39m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m◆\x1b[39m  Notes\n"
    "\x1b[36m│\x1b[39m  \x1b[90mSecret is read hidden from the terminal; "
    "code goes to the clipboard.\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)
SUCCESS_GOLDEN = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Generate TOTP\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Code copied: 287082\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)
ERROR_FRAME_GOLDEN = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Generate TOTP\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)


@dataclass
class CliHarness:
    stdout: io.StringIO = field(default_factory=io.StringIO)
    stderr: io.StringIO = field(default_factory=io.StringIO)
    copied: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ui = TwoFactorUi(self.stdout, self.stderr)

    def unexpected_read(self, _prompt: str) -> str:
        raise AssertionError("read_secret_fn should not be called")

    def unexpected_copy(self, _code: str) -> None:
        raise AssertionError("copy_fn should not be called")

    def copy(self, code: str) -> None:
        self.copied.append(code)

    @property
    def dependencies(self) -> dict[str, object]:
        return {
            "ui": self.ui,
            "read_secret_fn": self.unexpected_read,
            "copy_fn": self.unexpected_copy,
        }


@pytest.fixture
def harness() -> CliHarness:
    return CliHarness()


@pytest.mark.parametrize("arg", ["help", "-h", "--help"])
def test_help_aliases_exit_zero_and_match_js_bytes(
    arg: str, harness: CliHarness
) -> None:
    assert run_cli([arg], **harness.dependencies) == 0
    assert harness.stdout.getvalue() == HELP_GOLDEN
    assert harness.stderr.getvalue() == ""


@pytest.mark.parametrize("arg", ["foo", "--unknown", "--"])
def test_unknown_command_exits_one_with_error_and_full_help(
    arg: str, harness: CliHarness
) -> None:
    assert run_cli([arg], **harness.dependencies) == 1
    assert harness.stdout.getvalue() == HELP_GOLDEN
    assert harness.stderr.getvalue() == (
        f"\x1b[31m❌ Unknown command: {arg}\x1b[39m\n"
    )


def test_success_never_emits_secret(harness: CliHarness) -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    result = run_cli(
        [],
        read_secret_fn=lambda _prompt: secret,
        copy_fn=harness.copy,
        now=59,
        ui=harness.ui,
    )

    assert result == 0
    assert harness.copied == ["287082"]
    assert secret not in harness.stdout.getvalue()
    assert secret not in harness.stderr.getvalue()
    assert harness.stdout.getvalue() == SUCCESS_GOLDEN
    assert harness.stderr.getvalue() == ""


@pytest.mark.parametrize(
    ("read", "copy", "error_message"),
    [
        (lambda _prompt: "!!!!", lambda _code: None, "invalid Base32 character"),
        (
            lambda _prompt: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            lambda _code: (_ for _ in ()).throw(
                RuntimeError("failed to copy code to clipboard")
            ),
            "failed to copy code to clipboard",
        ),
        (
            lambda _prompt: (_ for _ in ()).throw(
                RuntimeError("interactive terminal required")
            ),
            lambda _code: None,
            "interactive terminal required",
        ),
    ],
)
def test_expected_failures_close_frame_and_match_js_bytes(
    harness: CliHarness,
    read: Callable[[str], str],
    copy: Callable[[str], None],
    error_message: str,
) -> None:
    result = run_cli([], read_secret_fn=read, copy_fn=copy, now=59, ui=harness.ui)

    assert result == 1
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == (
        f"\x1b[31m❌ {error_message}\x1b[39m\n"
    )


def test_unexpected_failure_is_not_hidden(harness: CliHarness) -> None:
    def fail_unexpectedly(_prompt: str) -> str:
        raise LookupError("unexpected")

    with pytest.raises(LookupError, match="^unexpected$"):
        run_cli([], read_secret_fn=fail_unexpectedly, ui=harness.ui)


class FakeTty:
    def __init__(
        self,
        *,
        line: str = "secret\n",
        read_error: BaseException | None = None,
    ) -> None:
        self.line = line
        self.read_error = read_error
        self.writes: list[str] = []
        self.closed = False

    def __enter__(self) -> "FakeTty":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def fileno(self) -> int:
        return 42

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        if self.read_error is not None:
            raise self.read_error
        return self.line


def _fake_tty_setup(monkeypatch: pytest.MonkeyPatch, tty: FakeTty):
    original = [1, 2, 3, 4 | cli.termios.ECHO, 5, 6, [7]]
    changes: list[list[object]] = []
    monkeypatch.setattr(cli.os, "open", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(builtins, "open", lambda *_args, **_kwargs: tty)
    monkeypatch.setattr(
        cli.os,
        "write",
        lambda _fd, data: tty.writes.append(data.decode("utf-8")) or len(data),
    )
    monkeypatch.setattr(cli.termios, "tcgetattr", lambda _fd: original)

    def set_attributes(_fd: int, when: int, attributes: list[object]) -> None:
        assert when == cli.termios.TCSADRAIN
        changes.append(attributes)

    monkeypatch.setattr(cli.termios, "tcsetattr", set_attributes)
    return original, changes


def test_read_secret_hides_echo_strips_newline_and_restores_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\r\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)

    result = read_secret("prompt: ", tty_path="ignored")

    assert result == "hidden"
    assert tty.writes == ["prompt: ", "\n"]
    assert changes[0][3] == original[3] & ~cli.termios.ECHO
    assert changes[1] == original
    assert tty.closed


def test_read_secret_translates_missing_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object):
        raise OSError("not a tty")

    monkeypatch.setattr(cli.os, "open", missing)

    with pytest.raises(RuntimeError, match="^interactive terminal required$"):
        read_secret("prompt: ", tty_path="missing")


@pytest.mark.parametrize(
    "read_error",
    [
        OSError("input failed"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte"),
    ],
)
def test_read_secret_restores_after_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    read_error: BaseException,
) -> None:
    tty = FakeTty(read_error=read_error)
    original, changes = _fake_tty_setup(monkeypatch, tty)

    with pytest.raises(RuntimeError, match="^interactive terminal required$"):
        read_secret("prompt: ", tty_path="ignored")

    assert changes[-1] == original
    assert tty.closed


def test_read_secret_restores_after_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(read_error=KeyboardInterrupt())
    original, changes = _fake_tty_setup(monkeypatch, tty)

    with pytest.raises(KeyboardInterrupt):
        read_secret("prompt: ", tty_path="ignored")

    assert changes[-1] == original
    assert tty.closed


def test_read_secret_reports_disable_failure_after_restoration_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty()
    original = [1, 2, 3, 4 | cli.termios.ECHO, 5, 6, [7]]
    calls: list[list[object]] = []
    monkeypatch.setattr(cli.os, "open", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(builtins, "open", lambda *_args, **_kwargs: tty)
    monkeypatch.setattr(cli.termios, "tcgetattr", lambda _fd: original)

    def fail_disable(_fd: int, _when: int, attributes: list[object]) -> None:
        calls.append(attributes)
        if len(calls) == 1:
            raise cli.termios.error("disable failed")

    monkeypatch.setattr(cli.termios, "tcsetattr", fail_disable)

    with pytest.raises(RuntimeError, match="^failed to disable terminal echo$"):
        read_secret("prompt: ", tty_path="ignored")

    assert calls[-1] == original
    assert tty.closed


def test_read_secret_reports_restore_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tty = FakeTty()
    original = [1, 2, 3, 4 | cli.termios.ECHO, 5, 6, [7]]
    calls = 0
    monkeypatch.setattr(cli.os, "open", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(builtins, "open", lambda *_args, **_kwargs: tty)
    monkeypatch.setattr(cli.os, "write", lambda _fd, data: len(data))
    monkeypatch.setattr(cli.termios, "tcgetattr", lambda _fd: original)

    def fail_restore(_fd: int, _when: int, _attributes: list[object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise cli.termios.error("restore failed")

    monkeypatch.setattr(cli.termios, "tcsetattr", fail_restore)

    with pytest.raises(RuntimeError, match="^failed to restore terminal echo$"):
        read_secret("prompt: ", tty_path="ignored")

    assert tty.closed


def test_main_exits_with_run_cli_result(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "argv", ["2fa", "help"])
    monkeypatch.setattr(cli, "run_cli", lambda argv: seen.append(list(argv)) or 7)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 7
    assert seen == [["help"]]
