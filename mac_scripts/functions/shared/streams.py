from typing import Protocol


class TextWriter(Protocol):
    def write(self, text: str) -> object: ...

    def isatty(self) -> bool: ...


def write_line(stream: TextWriter, text: str = "") -> None:
    stream.write(f"{text}\n")


def is_tty(stream: TextWriter) -> bool:
    return stream.isatty()
