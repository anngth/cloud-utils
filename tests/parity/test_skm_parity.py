from __future__ import annotations

from pathlib import Path
import shutil

import pytest


def assert_parity(
    skm_runner,
    tmp_path: Path,
    argv: tuple[str, ...],
    **kwargs: object,
) -> None:
    case_root = tmp_path / "case"
    expected = skm_runner("javascript", case_root, list(argv), **kwargs)
    shutil.rmtree(case_root)
    actual = skm_runner("python", case_root, list(argv), **kwargs)
    assert actual == expected


@pytest.mark.parametrize(
    "argv",
    [
        ("help",),
        ("-h",),
        ("--help",),
        ("wat",),
        ("constructor",),
        ("toString",),
        ("__proto__",),
        ("add",),
        ("remove", "--all", "extra"),
        ("status", "extra"),
        ("source",),
        ("source", "wat"),
        ("source", "add"),
        ("source", "edit"),
        ("source", "remove"),
        (
            "source",
            "add",
            "owner/catalog",
            "--all",
            "--no-skills",
        ),
    ],
)
def test_cli_grammar_matches_javascript(
    skm_runner,
    tmp_path: Path,
    argv: tuple[str, ...],
) -> None:
    assert_parity(skm_runner, tmp_path, argv)


def test_status_matches_javascript(skm_runner, tmp_path: Path) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("status",),
        sources={
            "version": 1,
            "sources": [
                {"source": "owner/catalog", "skills": ["demo"]}
            ],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0,
                "stdout": "[]\n",
                "stderr": "",
            }
        },
    )


def test_status_upstream_error_matches_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("status",),
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 7,
                "stdout": "",
                "stderr": "upstream failed\n",
            }
        },
    )


def test_status_extras_only_matches_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("status",),
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0,
                "stdout": (
                    '[{"name":"extra","path":"/skills/extra",'
                    '"scope":"project","agents":["Codex"]}]\n'
                ),
                "stderr": "",
            }
        },
    )


def test_status_invalid_config_matches_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("status",),
        sources=b"not json\n",
    )
