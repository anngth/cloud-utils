import pytest

from shared.streams import is_tty


class TtyResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def isatty(self) -> bool:
        return self.value


class BrokenTty:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def isatty(self) -> bool:
        raise self.error


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False)])
def test_is_tty_returns_normal_stream_value(value: bool, expected: bool) -> None:
    assert is_tty(TtyResult(value)) is expected


def test_is_tty_returns_false_without_isatty() -> None:
    assert is_tty(object()) is False


@pytest.mark.parametrize("error", [AttributeError("gone"), OSError("closed")])
def test_is_tty_returns_false_when_isatty_fails(error: Exception) -> None:
    assert is_tty(BrokenTty(error)) is False
