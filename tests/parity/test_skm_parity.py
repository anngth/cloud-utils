from __future__ import annotations

from pathlib import Path
import json
import shutil

import pytest


DISCOVERY = (
    Path(__file__).resolve().parents[2]
    / "mac_scripts/functions/skills-manager/__tests__/fixtures/"
    "discovery-flat.txt"
).read_text(encoding="utf-8")


def discovery(source: str, *, status: int = 0) -> dict[tuple[str, ...], object]:
    return {
        ("skills", "add", source, "--list"): {
            "status": status,
            "stdout": DISCOVERY if status == 0 else "",
            "stderr": "discovery failed\n" if status else "",
        }
    }


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
        (),
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
        ("source", "add", "owner/catalog", "-k"),
        ("source", "add", "owner/catalog", "--unknown"),
        ("source", "remove", "1", "--yes"),
        ("source", "remove", "1", "extra"),
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


@pytest.mark.parametrize(
    ("argv", "sources", "responses"),
    [
        (("source", "add", "owner/new", "--no-skills"), None, None),
        (
            ("source", "add", "owner/new", "--all", "--yes"),
            None,
            discovery("owner/new"),
        ),
        (
            (
                "source",
                "add",
                "owner/new",
                "-k",
                "react-best-practices",
                "--skill",
                "frontend-design",
                "-k",
                "react-best-practices",
            ),
            None,
            discovery("owner/new"),
        ),
        (
            ("source", "edit", "1", "--no-skills"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": ["old"]}],
            },
            None,
        ),
        (
            ("source", "edit", "owner/old", "--all"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": ["old"]}],
            },
            discovery("owner/old"),
        ),
        (
            ("source", "remove", "1"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": []}],
            },
            {
                ("skills", "list", "--json"): {
                    "status": 0,
                    "stdout": "[]\n",
                    "stderr": "",
                }
            },
        ),
        (
            ("source", "remove", "owner/old"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": []}],
            },
            {
                ("skills", "list", "--json"): {
                    "status": 0,
                    "stdout": "[]\n",
                    "stderr": "",
                }
            },
        ),
    ],
)
def test_source_success_paths_match_javascript(
    skm_runner,
    tmp_path: Path,
    argv: tuple[str, ...],
    sources: object,
    responses: object,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        argv,
        sources=sources,
        npx_responses=responses,
    )


@pytest.mark.parametrize(
    ("argv", "sources", "responses"),
    [
        (
            ("source", "add", "owner/new", "--all"),
            None,
            discovery("owner/new", status=7),
        ),
        (
            ("source", "add", "owner/new", "-k", "missing"),
            None,
            discovery("owner/new"),
        ),
        (
            ("source", "add", "owner/new", "--all"),
            {
                "version": 1,
                "sources": [
                    {
                        "source": "https://user:secret@example.com/private",
                        "skills": ["frontend-design"],
                    }
                ],
            },
            discovery("owner/new"),
        ),
        (
            ("source", "add", "owner/new"),
            None,
            discovery("owner/new"),
        ),
        (
            ("source", "add", "owner/old", "--no-skills"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": []}],
            },
            None,
        ),
        (
            ("source", "edit", "9", "--no-skills"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": []}],
            },
            None,
        ),
        (
            ("source", "remove", "owner/old"),
            {
                "version": 1,
                "sources": [{"source": "owner/old", "skills": []}],
            },
            {
                ("skills", "list", "--json"): {
                    "status": 7,
                    "stdout": "",
                    "stderr": "list failed\n",
                }
            },
        ),
    ],
)
def test_source_failure_paths_match_javascript_without_writes(
    skm_runner,
    tmp_path: Path,
    argv: tuple[str, ...],
    sources: object,
    responses: object,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        argv,
        sources=sources,
        npx_responses=responses,
    )


def test_source_remove_installed_block_matches_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    sources = {
        "version": 1,
        "sources": [{"source": "owner/old", "skills": ["demo"]}],
    }
    responses = {
        ("skills", "list", "--json"): {
            "status": 0,
            "stdout": (
                '[{"name":"demo","path":"/skills/demo",'
                '"scope":"project","agents":["Codex"]}]\n'
            ),
            "stderr": "",
        }
    }

    def prepare(root: Path) -> None:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "skills-lock.json").write_text(
            json.dumps({"skills": {"demo": {"source": "owner/old"}}}),
            encoding="utf-8",
        )

    case_root = tmp_path / "case"
    prepare(case_root)
    expected = skm_runner(
        "javascript",
        case_root,
        ["source", "remove", "owner/old"],
        sources=sources,
        npx_responses=responses,
    )
    shutil.rmtree(case_root)
    prepare(case_root)
    actual = skm_runner(
        "python",
        case_root,
        ["source", "remove", "owner/old"],
        sources=sources,
        npx_responses=responses,
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("shape", "source"),
    [
        ("missing", "./missing"),
        ("missing-quote", "./missing'quote"),
        ("missing-backslash", r"./missing\backslash"),
        ("missing-both", r"./missing'both\tail"),
        ("alias-missing", "./alias/missing"),
        ("dangling", "./dangling"),
        ("dangling-child", "./dangling/child"),
        ("child", "./file/child"),
        ("loop", "./loop-a"),
        ("loop-child", "./loop-a/child"),
        ("alias-child", "./alias/file/child"),
        ("nested-missing", "./outer/inner/missing"),
        ("nested-child", "./outer/inner/file/child"),
        ("nested-dangling", "./outer/bad/child"),
        ("nested-loop", "./outer/cycle/child"),
        ("valid", "./valid"),
        ("tmp-missing", "missing"),
        ("tmp-child", "file/child"),
    ],
)
def test_source_add_local_realpath_shapes_match_javascript(
    skm_runner,
    tmp_path: Path,
    shape: str,
    source: str,
) -> None:
    case_root = tmp_path / "case"
    external = Path("/tmp") / f"skm-{tmp_path.parent.name}-{tmp_path.name}"

    def run(runtime: str):
        project = case_root / "project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "target").mkdir()
        (project / "level").mkdir()
        (project / "target2").mkdir()
        (project / "file").write_text("file", encoding="utf-8")
        (project / "target/file").write_text("file", encoding="utf-8")
        (project / "target2/file").write_text("file", encoding="utf-8")
        (project / "alias").symlink_to("target")
        (project / "dangling").symlink_to("missing-target")
        (project / "loop-a").symlink_to("loop-b")
        (project / "loop-b").symlink_to("loop-a")
        (project / "outer").symlink_to("level")
        (project / "level/inner").symlink_to("../target2")
        (project / "level/bad").symlink_to("../absent")
        (project / "level/cycle").symlink_to("../loop-a")
        (project / "valid").symlink_to("target")
        actual_source = source
        if shape.startswith("tmp-"):
            shutil.rmtree(external, ignore_errors=True)
            external.mkdir()
            (external / "file").write_text("file", encoding="utf-8")
            actual_source = str(external / source)
        return skm_runner(
            runtime,
            case_root,
            ["source", "add", actual_source, "--no-skills"],
        )

    try:
        expected = run("javascript")
        shutil.rmtree(case_root)
        actual = run("python")
        assert actual == expected
    finally:
        shutil.rmtree(external, ignore_errors=True)


def test_source_remove_skips_missing_local_lock_source_like_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "case"
    responses = {
        ("skills", "list", "--json"): {
            "status": 0,
            "stdout": (
                '[{"name":"demo","path":"/skills/demo",'
                '"scope":"project","agents":["Codex"]}]\n'
            ),
            "stderr": "",
        }
    }

    def run(runtime: str):
        project = case_root / "project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "skills-lock.json").write_text(
            json.dumps({"skills": {"demo": {"source": "./missing"}}}),
            encoding="utf-8",
        )
        sources = {
            "version": 1,
            "sources": [
                {
                    "source": str((project / "missing").resolve()),
                    "skills": ["demo"],
                }
            ],
        }
        return skm_runner(
            runtime,
            case_root,
            ["source", "remove", "1"],
            sources=sources,
            npx_responses=responses,
        )

    expected = run("javascript")
    shutil.rmtree(case_root)
    actual = run("python")
    assert actual == expected


def test_source_add_preserves_well_formed_catalog_json_bytes(
    skm_runner,
    tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("source", "add", "owner/new", "--no-skills"),
        sources={
            "meta": {
                "high": "\ud800",
                "nested": [
                    {
                        "key\ud800": "value",
                        "low": "\udc00",
                        "pair": "\ud83d\ude00",
                    }
                ],
                "emoji": "😀",
                "line": "left\u2028right",
            },
            "version": 1,
            "sources": [
                {
                    "extra": {"second": 2, "first": 1},
                    "source": "owner/old",
                    "skills": [],
                }
            ],
        },
    )


def test_source_conflict_replaces_lone_surrogate_like_javascript(
    skm_runner,
    tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("source", "add", "owner/new", "--all"),
        sources={
            "version": 1,
            "sources": [
                {
                    "source": "opaque-\ud800",
                    "skills": ["frontend-design"],
                }
            ],
        },
        npx_responses=discovery("owner/new"),
    )


def test_add_partial_failure_and_retry_snapshot_matches_javascript(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "--all", "--yes"),
        sources={
            "version": 1,
            "sources": [
                {"source": "owner/a", "skills": ["one"]},
                {"source": "owner/b", "skills": ["two"]},
            ],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0, "stdout": "[]\n", "stderr": "",
            },
            ("skills", "add", "owner/a", "--skill", "one", "--yes"): {
                "status": 7, "stdout": "", "stderr": "first failed\n",
            },
            ("skills", "add", "owner/b", "--skill", "two", "--yes"): {
                "status": 0, "stdout": "second ok\n", "stderr": "",
            },
        },
    )


def test_add_success_snapshot_matches_javascript(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "1", "--yes"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0, "stdout": "[]\n", "stderr": "",
            },
            ("skills", "add", "owner/a", "--skill", "one", "--yes"): {
                "status": 0, "stdout": "installed\n", "stderr": "",
            },
        },
    )


@pytest.mark.parametrize("actual_source", ["owner/a", "owner/wrong"])
def test_add_installed_noop_and_mismatch_snapshots_match_javascript(
    skm_runner, tmp_path: Path, actual_source: str,
) -> None:
    case_root = tmp_path / "case"
    sources = {
        "version": 1,
        "sources": [{"source": "owner/a", "skills": ["one"]}],
    }
    responses = {
        ("skills", "list", "--json"): {
            "status": 0,
            "stdout": (
                '[{"name":"one","path":"/skills/one",'
                '"scope":"project","agents":["Codex"]}]\n'
            ),
            "stderr": "",
        },
    }

    def run(runtime: str):
        project = case_root / "project"
        project.mkdir(parents=True, exist_ok=True)
        (project / "skills-lock.json").write_text(
            json.dumps({"skills": {"one": {"source": actual_source}}}),
            encoding="utf-8",
        )
        return skm_runner(
            runtime,
            case_root,
            ["add", "1", "--yes"],
            sources=sources,
            npx_responses=responses,
        )

    expected = run("javascript")
    shutil.rmtree(case_root)
    assert run("python") == expected


def test_add_dry_run_snapshot_has_no_mutation_like_javascript(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "1", "--yes", "--dry-run"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0, "stdout": "[]\n", "stderr": "",
            },
        },
    )


def test_add_skill_selection_cancellation_matches_javascript(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "1"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0, "stdout": "[]\n", "stderr": "",
            },
        },
        stdin=b"q",
    )


def test_add_desired_conflict_matches_javascript_without_state_call(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "--all", "--yes"),
        sources={
            "version": 1,
            "sources": [
                {"source": "owner/a", "skills": ["same"]},
                {"source": "owner/b", "skills": ["same"]},
            ],
        },
    )


def test_add_untracked_conflict_matches_javascript_without_mutation(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "1", "--yes"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0,
                "stdout": (
                    '[{"name":"one","path":"/skills/one",'
                    '"scope":"project","agents":["Codex"]}]\n'
                ),
                "stderr": "",
            },
        },
    )


def test_remove_failure_retry_and_untracked_eligibility_match_javascript(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("remove", "1", "--yes"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one", "two"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0,
                "stdout": (
                    '[{"name":"one","path":"/skills/one",'
                    '"scope":"project","agents":["Codex"]},'
                    '{"name":"two","path":"/skills/two",'
                    '"scope":"project","agents":["Codex"]}]\n'
                ),
                "stderr": "",
            },
            ("skills", "remove", "one", "two", "--yes"): {
                "status": 4, "stdout": "", "stderr": "remove failed\n",
            },
        },
    )


def test_remove_dry_run_matches_javascript_without_mutation(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("remove", "--all", "--yes", "--dry-run"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/a", "skills": ["one"]}],
        },
        npx_responses={
            ("skills", "list", "--json"): {
                "status": 0, "stdout": "[]\n", "stderr": "",
            },
        },
    )


def test_add_empty_source_matches_javascript_without_state_call(
    skm_runner, tmp_path: Path,
) -> None:
    assert_parity(
        skm_runner,
        tmp_path,
        ("add", "1", "--yes"),
        sources={
            "version": 1,
            "sources": [{"source": "owner/empty", "skills": []}],
        },
    )
