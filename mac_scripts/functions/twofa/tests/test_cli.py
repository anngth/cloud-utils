import builtins
from dataclasses import dataclass, field
import io
import os
import signal
import sys
import threading
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
                cli.ClipboardError("failed to copy code to clipboard")
            ),
            "failed to copy code to clipboard",
        ),
        (
            lambda _prompt: (_ for _ in ()).throw(
                cli.TtyInputError("interactive terminal required")
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


def test_unexpected_failure_matches_javascript_framed_boundary(
    harness: CliHarness,
) -> None:
    def fail_unexpectedly(_prompt: str) -> str:
        raise LookupError("unexpected")

    result = run_cli([], read_secret_fn=fail_unexpectedly, ui=harness.ui)

    assert result == 1
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == "\x1b[31m❌ unexpected\x1b[39m\n"


def test_ctrl_c_uses_only_injected_streams_and_exact_frame(
    harness: CliHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_stderr = io.StringIO()

    monkeypatch.setattr(sys, "stderr", global_stderr)

    error = KeyboardInterrupt()

    def interrupt(_prompt: str) -> str:
        raise error

    with pytest.raises(KeyboardInterrupt) as error_info:
        run_cli([], read_secret_fn=interrupt, ui=harness.ui)

    assert error_info.value is error
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == ""
    assert global_stderr.getvalue() == ""


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
@pytest.mark.parametrize("operation", ["read", "copy"])
def test_unexpected_common_exceptions_use_framed_javascript_error(
    harness: CliHarness,
    error_type: type[Exception],
    operation: str,
) -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    message = f"unexpected {operation} detail"

    def read(_prompt: str) -> str:
        if operation == "read":
            raise error_type(message)
        return secret

    def copy(_code: str) -> None:
        if operation == "copy":
            raise error_type(message)

    result = run_cli(
        [], read_secret_fn=read, copy_fn=copy, now=59, ui=harness.ui
    )

    assert result == 1
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == f"\x1b[31m❌ {message}\x1b[39m\n"


@pytest.mark.parametrize(
    ("operation", "error_type", "message"),
    [
        ("read", RuntimeError, "interactive terminal required"),
        ("generate", ValueError, "invalid Base32 character"),
        ("copy", RuntimeError, "failed to copy code to clipboard"),
    ],
)
def test_same_message_non_domain_exceptions_still_use_framed_error(
    harness: CliHarness,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[Exception],
    message: str,
) -> None:
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def raise_collision() -> None:
        raise error_type(message)

    def read(_prompt: str) -> str:
        if operation == "read":
            raise_collision()
        return secret

    def copy(_code: str) -> None:
        if operation == "copy":
            raise_collision()

    if operation == "generate":
        monkeypatch.setattr(
            cli,
            "generate_totp",
            lambda *_args, **_kwargs: raise_collision(),
        )

    result = run_cli(
        [], read_secret_fn=read, copy_fn=copy, now=59, ui=harness.ui
    )

    assert result == 1
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == f"\x1b[31m❌ {message}\x1b[39m\n"


@pytest.mark.parametrize("error", [SystemExit(23), BaseException("stop now")])
def test_non_exception_base_failures_propagate_with_identity_after_frame_cleanup(
    harness: CliHarness, error: BaseException
) -> None:
    def fail(_prompt: str) -> str:
        raise error

    with pytest.raises(type(error)) as error_info:
        run_cli([], read_secret_fn=fail, ui=harness.ui)

    assert error_info.value is error
    assert harness.stdout.getvalue() == ERROR_FRAME_GOLDEN
    assert harness.stderr.getvalue() == ""


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

    def close(self) -> None:
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
    input_bytes = bytearray(tty.line.encode("utf-8"))
    real_read = os.read
    monkeypatch.setattr(cli.os, "open", lambda *_args, **_kwargs: 42)
    monkeypatch.setattr(builtins, "open", lambda *_args, **_kwargs: tty)
    monkeypatch.setattr(
        cli.os,
        "write",
        lambda _fd, data: tty.writes.append(data.decode("utf-8")) or len(data),
    )

    def read(fd: int, size: int) -> bytes:
        if fd != 42:
            return real_read(fd, size)
        if tty.read_error is not None:
            raise tty.read_error
        if not input_bytes:
            return b""
        chunk = bytes(input_bytes[:size])
        del input_bytes[:size]
        return chunk

    monkeypatch.setattr(cli.os, "read", read)
    monkeypatch.setattr(
        cli.select,
        "select",
        lambda readers, _writers, _errors: ([42], [], [])
        if 42 in readers
        else ([], [], []),
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


def test_read_secret_rejects_non_main_thread_before_opening_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(
        cli.os,
        "open",
        lambda path, _flags: opened.append(path) or 42,
    )

    def worker() -> None:
        try:
            read_secret("prompt: ", tty_path="must-not-open")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert opened == []
    assert len(errors) == 1
    assert type(errors[0]) is cli.TtyInputError
    assert str(errors[0]) == "interactive terminal required"


def test_read_secret_restores_signal_handlers_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\n")
    _fake_tty_setup(monkeypatch, tty)
    original_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }
    original_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    original_wakeup_fd = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(original_wakeup_fd)

    assert read_secret("prompt: ", tty_path="ignored") == "hidden"

    assert {
        signum: signal.getsignal(signum) for signum in original_handlers
    } == original_handlers
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == original_mask
    restored_wakeup_fd = signal.set_wakeup_fd(-1)
    signal.set_wakeup_fd(restored_wakeup_fd)
    assert restored_wakeup_fd == original_wakeup_fd


def test_signal_arriving_during_terminal_restore_runs_prior_handler_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)
    previous = signal.getsignal(signal.SIGTERM)
    events: list[tuple[bool, bool, bool]] = []

    def prior_handler(_signum: int, _frame: object) -> None:
        events.append(
            (
                tty.closed,
                signal.getsignal(signal.SIGTERM) is prior_handler,
                changes[-1] == original,
            )
        )

    signal.signal(signal.SIGTERM, prior_handler)
    original_setattr = cli.termios.tcsetattr
    restore_signal_sent = False

    def signal_during_restore(fd: int, when: int, attributes: list[object]) -> None:
        nonlocal restore_signal_sent
        original_setattr(fd, when, attributes)
        if attributes == original and not restore_signal_sent:
            restore_signal_sent = True
            os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(cli.termios, "tcsetattr", signal_during_restore)
    try:
        assert read_secret("prompt: ", tty_path="ignored") == "hidden"
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert events == [(True, True, True)]


def test_signal_arriving_during_stream_close_runs_prior_handler_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)
    previous = signal.getsignal(signal.SIGTERM)
    events: list[tuple[bool, bool, bool]] = []
    close_signal_sent = False

    def prior_handler(_signum: int, _frame: object) -> None:
        events.append(
            (
                tty.closed,
                signal.getsignal(signal.SIGTERM) is prior_handler,
                changes[-1] == original,
            )
        )

    original_close = tty.close

    def signal_during_close() -> None:
        nonlocal close_signal_sent
        original_close()
        if not close_signal_sent:
            close_signal_sent = True
            os.kill(os.getpid(), signal.SIGTERM)

    tty.close = signal_during_close
    signal.signal(signal.SIGTERM, prior_handler)
    try:
        assert read_secret("prompt: ", tty_path="ignored") == "hidden"
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert events == [(True, True, True)]


def test_signal_arriving_during_handler_restore_uses_restored_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)
    real_signal = signal.signal
    previous = signal.getsignal(signal.SIGTERM)
    events: list[tuple[bool, bool, bool]] = []
    restore_signal_sent = False

    def prior_handler(_signum: int, _frame: object) -> None:
        events.append(
            (
                tty.closed,
                signal.getsignal(signal.SIGTERM) is prior_handler,
                changes[-1] == original,
            )
        )

    real_signal(signal.SIGTERM, prior_handler)

    def signal_during_handler_restore(signum: int, handler: object):
        nonlocal restore_signal_sent
        result = real_signal(signum, handler)
        if (
            signum == signal.SIGTERM
            and handler is prior_handler
            and not restore_signal_sent
        ):
            restore_signal_sent = True
            os.kill(os.getpid(), signal.SIGTERM)
        return result

    monkeypatch.setattr(cli.signal, "signal", signal_during_handler_restore)
    try:
        assert read_secret("prompt: ", tty_path="ignored") == "hidden"
    finally:
        real_signal(signal.SIGTERM, previous)

    assert events == [(True, True, True)]


def test_signal_arriving_during_handler_installation_runs_callable_with_echo_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tty = FakeTty(line="hidden\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)
    real_signal = signal.signal
    previous = signal.getsignal(signal.SIGHUP)
    events: list[tuple[int, bool, bool, bool]] = []
    injected = False

    def prior_handler(signum: int, frame: object) -> None:
        events.append(
            (
                signum,
                frame is not None,
                tty.closed,
                bool(changes[-1][3] & cli.termios.ECHO),
            )
        )

    real_signal(signal.SIGHUP, prior_handler)

    def signal_with_injection(signum: int, handler: object):
        nonlocal injected
        result = real_signal(signum, handler)
        if signum == signal.SIGHUP and handler is not prior_handler and not injected:
            injected = True
            os.kill(os.getpid(), signal.SIGHUP)
        return result

    monkeypatch.setattr(cli.signal, "signal", signal_with_injection)
    try:
        assert read_secret("prompt: ", tty_path="ignored") == "hidden"
    finally:
        real_signal(signal.SIGHUP, previous)

    assert events == [(signal.SIGHUP, True, False, False)]
    assert tty.writes.count("prompt: ") == 1


@pytest.mark.parametrize("error_type", [OSError, UnicodeError])
def test_common_exception_from_prior_handler_propagates_after_exact_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    tty = FakeTty(line="hidden\n")
    original, changes = _fake_tty_setup(monkeypatch, tty)
    real_signal = signal.signal
    previous = signal.getsignal(signal.SIGHUP)
    events: list[tuple[int, bool, bool]] = []
    injected = False
    handler_error = error_type("interactive terminal required")

    def prior_handler(signum: int, _frame: object) -> None:
        events.append(
            (
                signum,
                tty.closed,
                bool(changes[-1][3] & cli.termios.ECHO),
            )
        )
        raise handler_error

    real_signal(signal.SIGHUP, prior_handler)

    def signal_with_injection(signum: int, handler: object):
        nonlocal injected
        result = real_signal(signum, handler)
        if signum == signal.SIGHUP and handler is not prior_handler and not injected:
            injected = True
            os.kill(os.getpid(), signal.SIGHUP)
        return result

    monkeypatch.setattr(cli.signal, "signal", signal_with_injection)
    try:
        with pytest.raises(error_type) as error_info:
            read_secret("prompt: ", tty_path="ignored")

        assert error_info.value is handler_error
        assert signal.getsignal(signal.SIGHUP) is prior_handler
    finally:
        real_signal(signal.SIGHUP, previous)

    assert events == [(signal.SIGHUP, False, False)]
    assert changes[-1] == original
    assert tty.closed


def test_read_secret_translates_missing_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object):
        raise OSError("not a tty")

    monkeypatch.setattr(cli.os, "open", missing)

    with pytest.raises(
        cli.TtyInputError,
        match="^interactive terminal required$",
    ):
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

    with pytest.raises(
        cli.TtyInputError,
        match="^interactive terminal required$",
    ):
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

    with pytest.raises(
        cli.TtyInputError,
        match="^failed to disable terminal echo$",
    ):
        read_secret("prompt: ", tty_path="ignored")

    assert calls[-1] == original
    assert tty.closed


def test_read_secret_reports_restore_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tty = FakeTty()
    original = [1, 2, 3, 4 | cli.termios.ECHO, 5, 6, [7]]
    calls = 0
    _fake_tty_setup(monkeypatch, tty)

    def fail_restore(_fd: int, _when: int, _attributes: list[object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise cli.termios.error("restore failed")

    monkeypatch.setattr(cli.termios, "tcsetattr", fail_restore)

    with pytest.raises(
        cli.TtyInputError,
        match="^failed to restore terminal echo$",
    ):
        read_secret("prompt: ", tty_path="ignored")

    assert tty.closed


def test_tty_input_error_is_a_runtime_error() -> None:
    assert issubclass(cli.TtyInputError, RuntimeError)


def test_main_exits_with_run_cli_result(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "argv", ["2fa", "help"])
    monkeypatch.setattr(cli, "run_cli", lambda argv: seen.append(list(argv)) or 7)

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 7
    assert seen == [["help"]]
