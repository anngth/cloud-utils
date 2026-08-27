import pytest

from shared.process import CommandResult
from twofa.clipboard import copy_to_clipboard


def test_copy_to_clipboard_sends_only_code_to_pbcopy() -> None:
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return CommandResult(0)

    copy_to_clipboard("287082", runner=runner)

    assert calls == [(["pbcopy"], {"input_text": "287082", "capture": True})]


def test_copy_to_clipboard_hides_code_when_pbcopy_fails() -> None:
    code = "287082"

    def runner(argv, **kwargs):
        return CommandResult(1, stderr="clipboard unavailable")

    with pytest.raises(RuntimeError, match="^failed to copy code to clipboard$") as error:
        copy_to_clipboard(code, runner=runner)

    assert code not in str(error.value)


@pytest.mark.parametrize("error_type", [FileNotFoundError, OSError])
def test_copy_to_clipboard_hides_code_when_pbcopy_cannot_launch(error_type) -> None:
    code = "287082"

    def runner(argv, **kwargs):
        raise error_type("clipboard unavailable")

    with pytest.raises(RuntimeError, match="^failed to copy code to clipboard$") as error:
        copy_to_clipboard(code, runner=runner)

    assert code not in str(error.value)
