import pytest

from shared.process import CommandResult
from twofa.clipboard import ClipboardError, copy_to_clipboard


def test_copy_to_clipboard_sends_only_code_to_pbcopy() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return CommandResult(0)

    copy_to_clipboard("287082", runner=runner)

    assert calls == [(["pbcopy"], {"input_text": "287082", "capture": "stderr"})]


def test_copy_to_clipboard_hides_code_when_pbcopy_fails() -> None:
    code = "287082"

    def runner(argv, **kwargs):
        return CommandResult(1, stderr="clipboard unavailable")

    with pytest.raises(
        ClipboardError,
        match="^failed to copy code to clipboard: clipboard unavailable$",
    ) as error:
        copy_to_clipboard(code, runner=runner)

    assert code not in str(error.value)


@pytest.mark.parametrize("stderr", ["", " \n\t"])
def test_copy_to_clipboard_uses_generic_failure_when_stderr_is_empty(
    stderr: str,
) -> None:
    def runner(argv, **kwargs):
        return CommandResult(1, stderr=stderr)

    with pytest.raises(
        ClipboardError,
        match="^failed to copy code to clipboard$",
    ):
        copy_to_clipboard("287082", runner=runner)


def test_copy_to_clipboard_trims_pbcopy_stderr_like_javascript() -> None:
    def runner(argv, **kwargs):
        return CommandResult(2, stdout="ignored output", stderr=" \n permission denied \t")

    with pytest.raises(ClipboardError) as error:
        copy_to_clipboard("287082", runner=runner)

    assert str(error.value) == "failed to copy code to clipboard: permission denied"


@pytest.mark.parametrize("error_type", [FileNotFoundError, OSError])
def test_copy_to_clipboard_hides_code_when_pbcopy_cannot_launch(error_type) -> None:
    code = "287082"

    def runner(argv, **kwargs):
        raise error_type("clipboard unavailable")

    with pytest.raises(
        ClipboardError,
        match="^failed to copy code to clipboard$",
    ) as error:
        copy_to_clipboard(code, runner=runner)

    assert code not in str(error.value)


def test_clipboard_error_is_a_runtime_error() -> None:
    assert issubclass(ClipboardError, RuntimeError)
