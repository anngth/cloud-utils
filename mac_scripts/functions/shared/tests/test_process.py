import subprocess
import sys

from shared.process import CommandResult, run_process


def test_run_process_captures_text_and_return_code() -> None:
    result = run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)",
        ],
        capture=True,
    )

    assert result == CommandResult(7, "out\n", "err\n")


def test_run_process_passes_input_without_shell() -> None:
    result = run_process(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        input_text="123456",
        capture=True,
    )

    assert result.stdout == "123456"


def test_captured_process_ignores_nonempty_parent_stdin_like_node() -> None:
    driver = (
        "import sys; from shared.process import run_process; "
        "result = run_process([sys.executable, '-c', "
        "'import sys; sys.stdout.write(sys.stdin.read(1))'], capture=True); "
        "print(repr(result.stdout))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", driver],
        input="reserved-for-parent",
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stdout == "''\n"
    assert completed.stderr == ""


def test_run_process_can_ignore_stdout_while_capturing_stderr() -> None:
    result = run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('discarded'); print('detail', file=sys.stderr)",
        ],
        capture="stderr",
    )

    assert result == CommandResult(0, "", "detail\n")


def test_run_process_inherits_streams_when_capture_is_disabled(capfd) -> None:
    result = run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(3)",
        ],
        capture=False,
    )

    assert result == CommandResult(3)
    captured = capfd.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n"
