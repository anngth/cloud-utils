import io
import re
import unittest

from list_cmd import partition_lists, run_list
from ui import Ui

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _plain_list(document, brew_state, columns=80):
    out = io.StringIO()
    err = io.StringIO()
    ui = Ui(stdout=out, stderr=err)
    code = run_list(ui, document, brew_state, columns)
    return code, strip_ansi(out.getvalue()), out.getvalue(), err.getvalue()


NINE_TITLES = [
    "Formulae · in list, installed",
    "Formulae · in list, not installed",
    "Formulae · installed, not in list",
    "Taps · in list, tapped",
    "Taps · in list, not tapped",
    "Taps · tapped, not in list",
    "Casks · in list, installed",
    "Casks · in list, not installed",
    "Casks · installed, not in list",
]


class PartitionListsTests(unittest.TestCase):
    def test_classifies_installed_missing_extra(self):
        p = partition_lists(
            {
                "formulas": ["bat", "missing"],
                "casks": ["cursor"],
                "taps": ["a/b"],
            },
            {
                "formulas": ["bat", "extra"],
                "casks": ["cursor", "slack"],
                "taps": ["a/b", "c/d"],
            },
        )
        self.assertEqual(p["formulas"]["installed"], ["bat"])
        self.assertEqual(p["formulas"]["missing"], ["missing"])
        self.assertEqual(p["formulas"]["extra"], ["extra"])
        self.assertEqual(p["casks"]["installed"], ["cursor"])
        self.assertEqual(p["casks"]["missing"], [])
        self.assertEqual(p["casks"]["extra"], ["slack"])
        self.assertEqual(p["taps"]["installed"], ["a/b"])
        self.assertEqual(p["taps"]["missing"], [])
        self.assertEqual(p["taps"]["extra"], ["c/d"])

    def test_skips_tap_extras_when_desired_taps_empty(self):
        p = partition_lists(
            {"formulas": [], "casks": [], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": ["homebrew/core"]},
        )
        self.assertEqual(p["taps"], {"installed": [], "missing": [], "extra": []})
        self.assertEqual(p["formulas"]["extra"], ["bat"])


class RunListTests(unittest.TestCase):
    def test_returns_zero(self):
        code, plain, _, err = _plain_list(
            {"formulas": ["bat"], "casks": [], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": []},
        )
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("bat", plain)

    def test_renders_all_nine_sections_when_populated(self):
        _, plain, _, _ = _plain_list(
            {
                "formulas": ["bat", "missing-f"],
                "casks": ["cursor", "missing-c"],
                "taps": ["a/b", "missing-t"],
            },
            {
                "formulas": ["bat", "extra-f"],
                "casks": ["cursor", "extra-c"],
                "taps": ["a/b", "extra-t"],
            },
        )
        for title in NINE_TITLES:
            self.assertIn(title, plain)
        self.assertIn("bat", plain)
        self.assertIn("missing-f", plain)
        self.assertIn("extra-f", plain)
        self.assertIn("a/b", plain)
        self.assertIn("missing-t", plain)
        self.assertIn("extra-t", plain)
        self.assertIn("cursor", plain)
        self.assertIn("missing-c", plain)
        self.assertIn("extra-c", plain)

    def test_omits_tap_sections_when_desired_taps_empty(self):
        _, plain, _, _ = _plain_list(
            {"formulas": ["bat"], "casks": [], "taps": []},
            {
                "formulas": ["bat"],
                "casks": [],
                "taps": ["homebrew/core"],
            },
        )
        self.assertNotIn("Taps ·", plain)
        self.assertNotIn("homebrew/core", plain)
        self.assertIn("Formulae · in list, installed", plain)

    def test_skips_empty_sections(self):
        _, plain, _, _ = _plain_list(
            {"formulas": ["bat", "missing"], "casks": ["slack"], "taps": []},
            {"formulas": ["bat"], "casks": ["slack", "extra-c"], "taps": []},
        )
        self.assertIn("Formulae · in list, installed", plain)
        self.assertIn("Formulae · in list, not installed", plain)
        self.assertIn("Casks · in list, installed", plain)
        self.assertIn("Casks · installed, not in list", plain)
        self.assertNotIn("Formulae · installed, not in list", plain)
        self.assertNotIn("Taps ·", plain)
        self.assertNotIn("Casks · in list, not installed", plain)

    def test_summary_uses_desired_counts(self):
        _, plain, _, _ = _plain_list(
            {"formulas": ["bat", "gh"], "casks": ["cursor"], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": []},
        )
        self.assertIn(
            "Desired vs installed · 2 formulae · 0 taps · 1 casks",
            plain,
        )

    def test_section_header_uses_pointer_and_count(self):
        _, plain, _, _ = _plain_list(
            {"formulas": ["bat"], "casks": [], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": []},
        )
        self.assertIn("▸ Formulae · in list, installed (1)", plain)

    def test_does_not_print_title_banner(self):
        _, plain, raw, _ = _plain_list(
            {"formulas": ["bat"], "casks": [], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": []},
        )
        self.assertNotIn("BREW DESIRED UPDATE", plain)
        self.assertNotIn("\033[42m", raw)

    def test_grid_is_column_major(self):
        # cell=58 → cols = (118+2)/(58+2) = 2 at columns=120
        names = ["x" * 58, "y" * 58, "z" * 58, "w" * 58]
        _, plain, _, _ = _plain_list(
            {"formulas": names, "casks": [], "taps": []},
            {"formulas": names, "casks": [], "taps": []},
            columns=120,
        )
        grid_lines = [
            line
            for line in plain.splitlines()
            if line.startswith("  x") or line.startswith("  y")
        ]
        self.assertEqual(len(grid_lines), 2)
        self.assertIn("z", grid_lines[0])
        self.assertIn("w", grid_lines[1])

    def test_narrow_columns_fall_back_to_120(self):
        name = "twenty-character-nm"  # 20 chars
        _, plain, _, _ = _plain_list(
            {"formulas": [name], "casks": [], "taps": []},
            {"formulas": [name], "casks": [], "taps": []},
            columns=10,
        )
        self.assertIn(name, plain)

    def test_uses_ui_active_for_summary(self):
        _, _, raw, _ = _plain_list(
            {"formulas": ["bat"], "casks": [], "taps": []},
            {"formulas": ["bat"], "casks": [], "taps": []},
        )
        self.assertIn("\033[32m◆ Desired vs installed", raw)
