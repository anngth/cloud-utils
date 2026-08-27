from shared.process import run_process


class ClipboardError(RuntimeError):
    pass


def copy_to_clipboard(text: str, *, runner=run_process) -> None:
    try:
        result = runner(["pbcopy"], input_text=text, capture=True)
    except OSError:
        raise ClipboardError("failed to copy code to clipboard") from None

    if result.returncode != 0:
        raise ClipboardError("failed to copy code to clipboard")
