import io
import re
import unittest

from ui import Ui

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class TitleTests(unittest.TestCase):
    def test_title_green_banner(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.title()
        plain = strip_ansi(buf.getvalue())
        self.assertIn("BREW DESIRED UPDATE", plain)
        self.assertIn("\033[42m", buf.getvalue())  # green bg in raw output

    def test_title_badge_has_three_leading_spaces_and_trailing_space(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.title()
        plain = strip_ansi(buf.getvalue())
        self.assertIn("   BREW DESIRED UPDATE ", plain)

    def test_title_uses_black_foreground_and_fg_bg_reset(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.title()
        raw = buf.getvalue()
        self.assertIn("\033[30m", raw)
        self.assertIn("\033[39m", raw)
        self.assertIn("\033[49m", raw)

    def test_title_has_blank_line_above_and_below_badge(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.title()
        lines = strip_ansi(buf.getvalue()).splitlines()
        self.assertEqual(lines[0], "")
        self.assertTrue(any("BREW DESIRED UPDATE" in line for line in lines))
        badge_index = next(
            i for i, line in enumerate(lines) if "BREW DESIRED UPDATE" in line
        )
        self.assertEqual(lines[badge_index + 1], "")


class ChromeTests(unittest.TestCase):
    def test_step_prints_green_diamond(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.step("Updating Homebrew")
        raw = buf.getvalue()
        self.assertIn("\033[32m", raw)
        self.assertIn("◇ Updating Homebrew", strip_ansi(raw))

    def test_step_inserts_blank_line_when_previous_was_not_blank(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.active("Done")
        ui.step("Next")
        lines = strip_ansi(buf.getvalue()).splitlines()
        self.assertEqual(lines, ["◆ Done", "", "◇ Next"])

    def test_active_prints_green_filled_diamond(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.active("Usage: bud [command]")
        raw = buf.getvalue()
        self.assertIn("\033[32m", raw)
        self.assertIn("◆ Usage: bud [command]", strip_ansi(raw))

    def test_warn_prints_yellow_square(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.warn("already in list")
        raw = buf.getvalue()
        self.assertIn("\033[33m", raw)
        self.assertIn("■ already in list", strip_ansi(raw))

    def test_info_uses_gray_247(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.info("Excluding 1 cask(s): slack")
        raw = buf.getvalue()
        self.assertIn("\033[38;5;247m", raw)
        self.assertNotIn("\033[90m", raw)
        self.assertNotIn("\033[38;5;240m", raw)
        self.assertIn("Excluding 1 cask(s): slack", strip_ansi(raw))

    def test_command_colors_dollar_prefix_green(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.command("$ brew update")
        raw = buf.getvalue()
        self.assertIn("\033[32m", raw)
        self.assertIn("$ brew update", strip_ansi(raw))
        dollar_end = raw.index("$") + 1
        self.assertIn("\033[39m", raw[dollar_end:])

    def test_command_passthrough_without_dollar(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.command("not a brew line")
        self.assertEqual(buf.getvalue(), "not a brew line\n")


class ErrorTests(unittest.TestCase):
    def test_error_writes_red_to_stderr_without_emoji(self):
        out = io.StringIO()
        err = io.StringIO()
        ui = Ui(stdout=out, stderr=err)
        ui.error("Homebrew not found.")
        self.assertEqual(out.getvalue(), "")
        raw = err.getvalue()
        self.assertIn("\033[31m", raw)
        self.assertIn("Homebrew not found.", strip_ansi(raw))
        self.assertNotIn("❌", raw)
        self.assertNotIn("\u274c", raw)


class UsageTests(unittest.TestCase):
    def test_usage_does_not_print_title_banner(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.usage()
        raw = buf.getvalue()
        plain = strip_ansi(raw)
        self.assertNotIn("BREW DESIRED UPDATE", plain)
        self.assertNotIn("\033[42m", raw)
        first = next(line for line in plain.splitlines() if line)
        self.assertEqual(first, "◆ Usage: bud [command]")

    def test_usage_prints_canonical_help(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.usage()
        plain = strip_ansi(buf.getvalue())
        expected_lines = [
            "◆ Usage: bud [command]",
            "bud (help | -h | --help)  Show this help",
            "◆ Update",
            "bud [(-e | --exclude) <cask>...]  Update brew; upgrade desired casks",
            "◆ Lists",
            "bud (ls | list)  Desired vs installed (formulae, taps, casks)",
            "bud add <name...> [--cask | --formula | --tap]",
            "bud remove <name...>",
            "◆ Options",
            "--cask  Force cask type (add only)",
            "--formula  Force formula type (add only)",
            "--tap  Force tap type (add only)",
            "◆ Notes",
            "Bare bud does not install missing packages; it upgrades installed desired casks.",
            "user/repo = tap; user/repo/formula adds tap + formula.",
        ]
        for line in expected_lines:
            self.assertIn(line, plain)

    def test_usage_syntax_is_green_and_descriptions_are_gray_240(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.usage()
        raw = buf.getvalue()
        self.assertIn("\033[32mbud (help | -h | --help)\033[39m", raw)
        self.assertIn("\033[38;5;240mShow this help\033[39m", raw)

    def test_usage_section_headers_use_active_style(self):
        buf = io.StringIO()
        ui = Ui(stdout=buf)
        ui.usage()
        raw = buf.getvalue()
        self.assertIn("\033[32m◆ Update", raw)
        self.assertIn("\033[32m◆ Lists", raw)
        self.assertIn("\033[32m◆ Options", raw)
        self.assertIn("\033[32m◆ Notes", raw)
