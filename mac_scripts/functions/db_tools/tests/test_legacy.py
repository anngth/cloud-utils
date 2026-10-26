"""Tests for the one-way conversion of the old zsh-array secrets file."""

from __future__ import annotations

import re

import pytest

from db_tools.legacy import LegacySecretsError, convert_legacy_secrets, read_legacy_arrays
from db_tools.models import MongoSource, PostgresSource, PostgresTarget, VpsTunnel
from db_tools.secrets import parse_secrets_text

VPS_ROW = "vps|u|10.0.0.1|pg|5432|127.0.0.1|5432"


def secrets_text(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# --- the zsh subset that is read ---------------------------------------------------


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
    assert read_legacy_arrays(text) == {"VPS_CONFIGS": [VPS_ROW]}


def test_escaped_characters_in_values() -> None:
    arrays = read_legacy_arrays(
        secrets_text("typeset -a VPS_CONFIGS=(", r'"a\"b"', r'"a\\b"', r'"p\$ss"', ")")
    )
    assert arrays["VPS_CONFIGS"] == ['a"b', "a\\b", "p$ss"]


@pytest.mark.parametrize("row", ['"$HOME/x"', '"pass${X}"', '"a$"'])
def test_unescaped_dollar_is_rejected_instead_of_kept_literally(row: str) -> None:
    text = secrets_text("typeset -a VPS_CONFIGS=(", row, ")")
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(text)
    assert excinfo.value.line == 2
    assert "\\$" in str(excinfo.value)


def test_duplicate_array_name_raises() -> None:
    text = secrets_text(
        "typeset -a VPS_CONFIGS=(", f'    "{VPS_ROW}"', ")", "typeset -a VPS_CONFIGS=(", ")"
    )
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(text)
    assert excinfo.value.line == 4


def test_non_typeset_line_rejects_the_whole_file() -> None:
    text = secrets_text("typeset -a VPS_CONFIGS=(", f'    "{VPS_ROW}"', ")", "echo hi")
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(text)
    assert excinfo.value.line == 4


def test_unclosed_array_names_its_opening_line() -> None:
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(secrets_text("", "typeset -a VPS_CONFIGS=(", f'  "{VPS_ROW}"'))
    assert excinfo.value.line == 2


@pytest.mark.parametrize(
    "row",
    ['"x|postgresql://$(touch pwned)|db"', '"x|mongodb://host`whoami`"'],
)
def test_command_substitution_and_backticks_are_rejected(row: str) -> None:
    text = secrets_text("typeset -a POSTGRES_TARGETS=(", row, ")")
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(text)
    assert excinfo.value.line == 2


@pytest.mark.parametrize("row", ["vps-name", "'vps'"])
def test_bare_and_single_quoted_tokens_are_rejected(row: str) -> None:
    text = secrets_text("typeset -a VPS_CONFIGS=(", row, ")")
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(text)
    assert excinfo.value.line == 2


def test_unknown_array_is_rejected() -> None:
    with pytest.raises(LegacySecretsError) as excinfo:
        read_legacy_arrays(secrets_text("typeset -a OTHER=(", ")"))
    assert excinfo.value.line == 1


# --- conversion -----------------------------------------------------------------


def test_every_section_round_trips_through_the_toml_loader() -> None:
    text = secrets_text(
        "typeset -a VPS_CONFIGS=(",
        f'  "{VPS_ROW}"',
        ")",
        "typeset -a POSTGRES_SOURCES=(",
        '  "prod|postgresql://u:p@h:5432/app|app"',
        '  "rep|postgresql://u:p@h:5432/postgres|reports"',
        ")",
        "typeset -a POSTGRES_TARGETS=(",
        '  "one|postgresql://u:p@h:5432/db?sslmode=require"',
        ")",
        "typeset -a MONGO_SOURCES=(",
        '  "m|mongodb://u:p@h:27017|app, logs"',
        ")",
        "typeset -a MONGO_TARGETS=(",
        '  "mt|mongodb://u:p@h:27017"',
        ")",
    )
    conversion = convert_legacy_secrets(text)
    assert conversion.skipped == ()
    secrets = parse_secrets_text(conversion.toml)
    assert secrets.tunnels == (VpsTunnel("vps", "u", "10.0.0.1", "pg", 5432, "127.0.0.1", 5432),)
    assert secrets.postgres_sources == (
        PostgresSource("prod", "postgresql://u:p@h:5432/app", "app"),
        PostgresSource("rep", "postgresql://u:p@h:5432/postgres", "reports"),
    )
    assert secrets.postgres_targets == (
        PostgresTarget("one", "postgresql://u:p@h:5432/db?sslmode=require"),
    )
    assert secrets.mongo_sources == (MongoSource("m", "mongodb://u:p@h:27017", ("app", "logs")),)
    assert [t.name for t in secrets.mongo_targets] == ["mt"]


def test_database_is_written_only_when_it_differs_from_the_url_path() -> None:
    text = secrets_text(
        "typeset -a POSTGRES_SOURCES=(",
        '  "prod|postgresql://u:p@h:5432/app|app"',
        '  "rep|postgresql://u:p@h:5432/postgres|reports"',
        ")",
    )
    toml = convert_legacy_secrets(text).toml
    assert toml.count("database = ") == 1
    assert 'database = "reports"' in toml


def test_special_characters_survive_the_conversion() -> None:
    weird = 'postgresql://u:p"\\$#@?\x7f\u00e9@h:5432/db'  # `|` cannot occur in a row
    text = secrets_text(
        "typeset -a POSTGRES_TARGETS=(",
        '  "t|' + weird.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"',
        ")",
    )
    secrets = parse_secrets_text(convert_legacy_secrets(text).toml)
    assert secrets.postgres_targets[0].url == weird


@pytest.mark.parametrize(
    ("array", "row"),
    [
        ("VPS_CONFIGS", "bad|row"),
        ("VPS_CONFIGS", "v|me|10.0.0.1|postgres|x|db.internal|5432"),
        ("VPS_CONFIGS", "v|me|10.0.0.1|postgres|70000|db.internal|5432"),
        ("VPS_CONFIGS", "v|me|10.0.0.1|postgres|0|db.internal|5432"),
        ("VPS_CONFIGS", "v||10.0.0.1|postgres|5432|db.internal|5432"),
        ("POSTGRES_SOURCES", "prod|postgresql://u:p@h:5432/db"),
        ("POSTGRES_TARGETS", "onlyname"),
        ("POSTGRES_TARGETS", "one|postgresql://u:p@h:5432/postgres|postgres"),
        ("MONGO_SOURCES", "prod|mongodb://u:p@h:27017"),
        ("MONGO_SOURCES", "prod|mongodb://u:p@h:27017|app,,logs"),
        ("MONGO_TARGETS", "one|mongodb://u:p@h:27017|app"),
    ],
)
def test_invalid_rows_become_comments_and_are_reported(array: str, row: str) -> None:
    conversion = convert_legacy_secrets(secrets_text(f"typeset -a {array}=(", f'  "{row}"', ")"))
    assert conversion.skipped == (row,)
    assert "# not converted (invalid " in conversion.toml
    assert f"row): {row}" in conversion.toml
    assert not re.search(r"^\[\[", conversion.toml, re.MULTILINE)  # no entry was emitted
    assert parse_secrets_text(conversion.toml).tunnels == ()


def test_control_characters_in_a_skipped_row_are_escaped_in_the_comment() -> None:
    row = "ctl|postgresql://u:p\x01@h:5432/db|x"
    text = secrets_text("typeset -a POSTGRES_TARGETS=(", f'  "{row}"', ")")
    conversion = convert_legacy_secrets(text)
    assert conversion.skipped == (row,)
    assert "row): ctl|postgresql://u:p\\u0001@h:5432/db|x" in conversion.toml
    assert "\x01" not in conversion.toml
    assert parse_secrets_text(conversion.toml).postgres_targets == ()


def test_empty_arrays_convert_to_a_header_only_file() -> None:
    conversion = convert_legacy_secrets("typeset -a VPS_CONFIGS=(\n)\n")
    assert conversion.skipped == ()
    assert not re.search(r"^\[\[", conversion.toml, re.MULTILINE)
    assert parse_secrets_text(conversion.toml).tunnels == ()
