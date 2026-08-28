from pathlib import Path


def test_oracle_captures_streams_calls_and_config(tmp_path: Path, skm_runner) -> None:
    result = skm_runner(
        "javascript",
        tmp_path,
        ["source", "add", "anthropics/skills", "--no-skills"],
    )
    assert result.returncode == 0
    assert result.stderr == b""
    assert b"Source added: anthropics/skills" in result.stdout
    assert result.calls == ()
    assert result.files["skm/sources.json"] == (
        b'{\n  "version": 1,\n  "sources": [\n'
        b'    {\n      "source": "anthropics/skills",\n'
        b'      "skills": []\n    }\n  ]\n}\n'
    )
