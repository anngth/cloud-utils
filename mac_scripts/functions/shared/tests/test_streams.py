import io

from shared.streams import is_tty, write_line


def test_write_line_preserves_injected_stream() -> None:
    stream = io.StringIO()

    write_line(stream, "hello")

    assert stream.getvalue() == "hello\n"


def test_is_tty_delegates_to_stream() -> None:
    stream = io.StringIO()

    assert is_tty(stream) is False
