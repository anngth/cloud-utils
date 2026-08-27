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
