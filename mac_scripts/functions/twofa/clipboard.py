from shared.process import run_process


def copy_to_clipboard(text: str, *, runner=run_process) -> None:
    try:
        result = runner(["pbcopy"], input_text=text, capture=True)
    except OSError:
        raise RuntimeError("failed to copy code to clipboard") from None

    if result.returncode != 0:
        raise RuntimeError("failed to copy code to clipboard")
