import io

from mac_scripts.functions.shared.ui import FrameUi


EXPECTED_FRAME_BYTES = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Usage: 2fa\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m◆\x1b[39m  Notes\n"
    "\x1b[36m│\x1b[39m  \x1b[32m■\x1b[39m Ready\n"
    "\x1b[36m│\x1b[39m      \x1b[90msecondary\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)


def test_frame_bytes_use_green_badge_and_reset_colors() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = FrameUi(stdout, stderr)
    ui.title("2FA")
    ui.step("Usage: 2fa")
    ui.section("Notes")
    ui.item("Ready")
    ui.detail("secondary")
    ui.end()

    assert stdout.getvalue() == EXPECTED_FRAME_BYTES
    assert "\x1b[42m\x1b[30m 2FA " in stdout.getvalue()
    assert "\x1b[46m" not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_error_writes_one_cross_to_stderr() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = FrameUi(stdout, stderr)

    ui.error("failed")

    assert stdout.getvalue() == ""
    assert stderr.getvalue().count("❌") == 1
    assert stderr.getvalue() == "\x1b[31m❌ failed\x1b[39m\n"


def test_item_tones_markers_and_unknown_tone_fallback() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = FrameUi(stdout, stderr)

    ui.item("warn", tone="warning")
    ui.item("fail", tone="failure")
    ui.item("quiet", tone="muted")
    ui.item("custom", tone="not-a-tone")
    ui.item("manual", tone="warning", marker="!")

    assert stderr.getvalue() == ""
    assert stdout.getvalue() == (
        "\x1b[36m│\x1b[39m  \x1b[33m■\x1b[39m warn\n"
        "\x1b[36m│\x1b[39m  \x1b[31m■\x1b[39m fail\n"
        "\x1b[36m│\x1b[39m  \x1b[90m□\x1b[39m quiet\n"
        "\x1b[36m│\x1b[39m  \x1b[90m□\x1b[39m custom\n"
        "\x1b[36m│\x1b[39m  \x1b[33m!\x1b[39m manual\n"
    )


def test_multiline_items_and_details_stay_inside_frame() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = FrameUi(stdout, stderr)

    ui.item("first\nsecond\r\nthird")
    ui.detail("fourth\nfifth")

    assert stderr.getvalue() == ""
    assert stdout.getvalue() == (
        "\x1b[36m│\x1b[39m  \x1b[32m■\x1b[39m first\n"
        "\x1b[36m│\x1b[39m      \x1b[90msecond\x1b[39m\n"
        "\x1b[36m│\x1b[39m      \x1b[90mthird\x1b[39m\n"
        "\x1b[36m│\x1b[39m      \x1b[90mfourth\x1b[39m\n"
        "\x1b[36m│\x1b[39m      \x1b[90mfifth\x1b[39m\n"
    )


def test_help_rows_and_prompt_text_use_frame_primitives() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = FrameUi(stdout, stderr)

    ui.line()
    ui.command("2fa", "Prompt for Base32 secret")
    ui.note("Secret stays hidden")
    prompt = ui.prompt_text("Base32 secret: ")

    assert prompt == "\x1b[36m◆\x1b[39m  Base32 secret: "
    assert stdout.getvalue() == (
        "\n"
        "\x1b[36m│\x1b[39m  \x1b[32m2fa\x1b[39m  \x1b[90mPrompt for Base32 secret\x1b[39m\n"
        "\x1b[36m│\x1b[39m  \x1b[90mSecret stays hidden\x1b[39m\n"
    )
    assert stderr.getvalue() == ""
