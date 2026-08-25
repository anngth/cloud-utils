"""CLI contract tests driven by cli-contract.json."""

import io
import json
import os
import re
import subprocess
import tempfile
import unittest

from brew import format_brew_command
from ui import Ui

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
CONTRACT_PATH = os.path.join(os.path.dirname(__file__), "cli-contract.json")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class FakeRunner:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def run(self, args, ui):
        ui.command(format_brew_command(args))
        key = tuple(args)
        if key in self.mapping:
            code, stdout = self.mapping[key]
            return subprocess.CompletedProcess(
                args=args, returncode=code, stdout=stdout, stderr=""
            )
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )


def _state_map(detect=None):
    mapping = {
        ("list", "--formula"): (0, "bat\n"),
        ("list", "--cask", "--full-name"): (0, "cursor\n"),
        ("tap",): (0, ""),
        ("update",): (0, ""),
        ("upgrade", "--formula", "-y"): (0, ""),
        ("tap", "--repair"): (0, ""),
        ("upgrade", "--cask", "-y", "cursor"): (0, ""),
        ("cleanup", "--prune=1"): (0, ""),
    }
    for name, kinds in (detect or {}).items():
        mapping[("info", "--formula", name)] = (
            (0, f"==> {name}: desc\n") if "formula" in kinds else (1, "")
        )
        mapping[("info", "--cask", name)] = (
            (0, f"==> {name}: desc\n") if "cask" in kinds else (1, "")
        )
    return mapping


def _load_cases():
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        cases = json.load(handle)
    if not isinstance(cases, list):
        raise ValueError("cli-contract.json must be a list of cases")
    return cases


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.env = {
            "CLOUD_UTILS_CONFIG_DIR": self.root.name,
            "HOME": self.root.name,
            "PATH": "",
        }
        self.desired = os.path.join(self.root.name, "bud", "desired.json")
        os.makedirs(os.path.dirname(self.desired))

    def _seed_desired(self, case):
        seed = case.get("seed", "valid")
        if seed == "missing":
            return
        if seed == "invalid":
            with open(self.desired, "w", encoding="utf-8") as handle:
                handle.write("{not-json")
            return
        with open(self.desired, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "formulas": ["bat"],
                    "casks": ["cursor"],
                    "taps": [],
                },
                handle,
            )
            handle.write("\n")

    def _run_case(self, case):
        from cli import main

        self._seed_desired(case)
        out = io.StringIO()
        err = io.StringIO()
        resolve_brew = (
            (lambda _env: None)
            if case.get("brewMissing")
            else (lambda _env: "/opt/homebrew/bin/brew")
        )
        code = main(
            case["argv"],
            env=self.env,
            ui=Ui(stdout=out, stderr=err),
            runner=FakeRunner(_state_map(detect=case.get("detect"))),
            columns=80,
            resolve_brew=resolve_brew,
        )
        raw_out = out.getvalue()
        stdout = strip_ansi(raw_out)
        stderr = strip_ansi(err.getvalue())
        self.assertEqual(code, case["exitCode"], case["id"])
        for needle in case.get("stdoutContains") or []:
            self.assertIn(needle, stdout, case["id"])
        for needle in case.get("stdoutNotContains") or []:
            self.assertNotIn(needle, stdout, case["id"])
        for needle in case.get("stderrContains") or []:
            self.assertIn(needle, stderr, case["id"])
        for needle in case.get("stderrNotContains") or []:
            self.assertNotIn(needle, stderr, case["id"])
        for needle in case.get("rawStdoutContains") or []:
            self.assertIn(needle, raw_out, case["id"])

    def test_contract_has_at_least_20_cases(self):
        self.assertGreaterEqual(len(_load_cases()), 20)


def _install_generated_tests():
    try:
        cases = _load_cases()
    except FileNotFoundError:
        return
    seen = set()
    for case in cases:
        case_id = case["id"]
        if case_id in seen:
            raise ValueError(f"duplicate contract id: {case_id}")
        seen.add(case_id)
        method_name = "test_" + case_id.replace("-", "_")

        def _test(self, _case=case):
            self._run_case(_case)

        setattr(ContractTests, method_name, _test)


_install_generated_tests()


if __name__ == "__main__":
    unittest.main()
