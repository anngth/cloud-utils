"""Tests for the dbt secrets zsh-array parser."""

from __future__ import annotations

import pytest

from db_tools.models import RawSecrets
from db_tools.secrets import SecretsError, parse_secrets_text, redact

VPS_ROW = "vps|u|10.0.0.1|pg|5432|127.0.0.1|5432"


def secrets_text(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def test_comments_and_blank_lines_around_one_vps_row() -> None:
    text = secrets_text(
        "#!/bin/zsh",
        "",
        "# one VPS tunnel for this host",
        "typeset -a VPS_CONFIGS=(",
        "    # commented-out example row",
        "",
        f'    "{VPS_ROW}"',
        ")",
        "",
    )
    result = parse_secrets_text(text)
    assert isinstance(result, RawSecrets)
    assert result.vps == (VPS_ROW,)
    assert result.postgres_sources == ()
    assert result.postgres_targets == ()
    assert result.mongo_sources == ()
    assert result.mongo_targets == ()


def test_escaped_characters_in_values() -> None:
    result = parse_secrets_text(
        secrets_text(
            "typeset -a VPS_CONFIGS=(",
            r'"a\"b"',
            r'"a\\b"',
            r'"p\$ss"',
            ")",
        )
    )
    assert result.vps == ('a"b', "a\\b", "p$ss")


@pytest.mark.parametrize("row", ['"$HOME/x"', '"pass${X}"', '"a$"'])
def test_unescaped_dollar_is_rejected_instead_of_kept_literally(row: str) -> None:
    text = secrets_text("typeset -a VPS_CONFIGS=(", row, ")")
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert excinfo.value.line == 2
    assert "\\$" in str(excinfo.value)


def test_duplicate_array_name_raises() -> None:
    text = secrets_text(
        "typeset -a VPS_CONFIGS=(",
        f'    "{VPS_ROW}"',
        ")",
        "typeset -a VPS_CONFIGS=(",
        ")",
    )
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert excinfo.value.line == 4


def test_non_typeset_line_rejects_the_whole_file() -> None:
    text = secrets_text(
        "typeset -a VPS_CONFIGS=(",
        f'    "{VPS_ROW}"',
        ")",
        "echo hi",
    )
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert excinfo.value.line == 4


@pytest.mark.parametrize(
    "row",
    ['"x|postgresql://$(touch pwned)|db"', '"x|mongodb://host`whoami`"'],
)
def test_command_substitution_and_backticks_are_rejected(row: str) -> None:
    text = secrets_text(
        "typeset -a POSTGRES_TARGETS=(",
        row,
        ")",
    )
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert excinfo.value.line == 2


@pytest.mark.parametrize("row", ["vps-name", "'vps'"])
def test_bare_and_single_quoted_tokens_are_rejected(row: str) -> None:
    text = secrets_text(
        "typeset -a VPS_CONFIGS=(",
        row,
        ")",
    )
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert excinfo.value.line == 2


def test_empty_array_yields_empty_tuple() -> None:
    result = parse_secrets_text("typeset -a VPS_CONFIGS=(\n)\n")
    assert result.vps == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("name|postgresql://svc:s3cr3t@h:5432/db|db", "name|postgresql://***@h:5432/db|db"),
        ("mongodb+srv://u:p@ss@cluster.example/app", "mongodb+srv://***@cluster.example/app"),
        ("a|me|10.0.0.1|pg|5432|db|5432", "a|me|10.0.0.1|pg|5432|db|5432"),
        ("mongodb://cluster.example:27017", "mongodb://cluster.example:27017"),
    ],
)
def test_redact_masks_credentials_only(text: str, expected: str) -> None:
    assert redact(text) == expected
