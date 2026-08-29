from typing import Protocol

class TextWriter(Protocol):
    def write(self, text: str) -> object: ...
    def flush(self) -> object: ...

    def isatty(self) -> bool: ...

def is_tty(stream: object) -> bool:
    try:
        return bool(stream.isatty())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
