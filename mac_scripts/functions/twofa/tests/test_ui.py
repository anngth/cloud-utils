import io

from twofa.ui import TwoFactorUi


HELP_GOLDEN = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Usage: 2fa [command]\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m│\x1b[39m  \x1b[32m2fa\x1b[39m  "
    "\x1b[90mPrompt for Base32 secret; copy 6-digit TOTP\x1b[39m\n"
    "\x1b[36m│\x1b[39m  \x1b[32m2fa (help | -h | --help)\x1b[39m  "
    "\x1b[90mShow this help\x1b[39m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m◆\x1b[39m  Notes\n"
    "\x1b[36m│\x1b[39m  \x1b[90mSecret is read hidden from the terminal; "
    "code goes to the clipboard.\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)

SUCCESS_GOLDEN = (
    "\n"
    "   \x1b[42m\x1b[30m 2FA \x1b[39m\x1b[49m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Generate TOTP\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[32m◇\x1b[39m  Code copied: 287082\n"
    "\x1b[36m│\x1b[39m\n"
    "\x1b[36m└\x1b[39m\n"
)


def test_usage_matches_javascript_bytes() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = TwoFactorUi(stdout, stderr)

    ui.usage()

    assert stdout.getvalue() == HELP_GOLDEN
    assert stderr.getvalue() == ""


def test_interactive_flow_is_one_frame_and_matches_javascript_bytes() -> None:
    stdout, stderr = io.StringIO(), io.StringIO()
    ui = TwoFactorUi(stdout, stderr)

    ui.begin_totp()
    prompt = ui.secret_prompt()
    ui.success_copied("287082")

    assert prompt == "\x1b[36m◆\x1b[39m  Base32 secret: "
    assert stdout.getvalue() == SUCCESS_GOLDEN
    assert stdout.getvalue().count(" 2FA ") == 1
    assert stderr.getvalue() == ""
