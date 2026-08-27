from datetime import datetime, timezone
import json
import subprocess

import pytest

from git_tools.last_backup import (
    format_last_backup_label,
    format_last_checked_label,
    format_timestamp_label,
    parse_js_timestamp,
)


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "2026-02-31T00:00:00Z",
        "2023-02-29T24:00:00.000Z",
        "2024-02-30T12:34:56+07:00",
        "2026-08-08T09:30:00.123456789Z",
        "2026-08-08T09:30:00.1-0230",
        "2026-02-31T00:00:00",
        "0000-02-30T24:00:00Z",
        "2026-01-32T00:00:00Z",
        "2026-08-08T24:00:00.001Z",
        "2026-08-08T00:00:00+24:00",
        "20260808",
        "2026-W32-5",
        " 2026-08-08T00:00:00Z ",
    ],
)
def test_parse_js_timestamp_matches_node_normalization(value: object) -> None:
    script = (
        "const value = JSON.parse(process.argv[1]); "
        "const parsed = new Date(value).getTime(); "
        "process.stdout.write(JSON.stringify(Number.isNaN(parsed) ? null : parsed));"
    )
    node = subprocess.run(
        ["node", "-e", script, json.dumps(value)],
        text=True,
        capture_output=True,
        check=True,
    )

    assert parse_js_timestamp(value) == json.loads(node.stdout)


def test_format_last_backup_label_uses_relative_and_local_datetime() -> None:
    label = format_last_backup_label("2026-08-08T08:00:00.000Z", NOW)
    assert label.startswith("Last backup: 2 hours ago (")
    assert label.endswith(")")
    assert len(label.rsplit("(", 1)[1][:-1]) == 16


def test_format_last_backup_label_uses_just_now_under_one_minute() -> None:
    now = datetime(2026, 8, 8, 10, 0, 30, tzinfo=timezone.utc)
    assert format_last_backup_label("2026-08-08T10:00:00.000Z", now).startswith(
        "Last backup: just now ("
    )


@pytest.mark.parametrize(
    ("iso_string", "fragment"),
    [
        ("2026-08-08T09:59:00.000Z", "1 minute ago"),
        ("2026-08-08T09:00:00.000Z", "1 hour ago"),
        ("2026-08-07T10:00:00.000Z", "1 day ago"),
    ],
)
def test_format_timestamp_label_uses_singular_relative_units(
    iso_string: str, fragment: str
) -> None:
    assert fragment in format_timestamp_label(iso_string, prefix="Last backup", now=NOW)


def test_format_last_checked_label_uses_last_checked_prefix() -> None:
    assert format_last_checked_label("2026-08-08T08:00:00.000Z", NOW).startswith(
        "Last checked: 2 hours ago ("
    )


@pytest.mark.parametrize("prefix", ["Last backup", "Last checked"])
def test_format_timestamp_label_soft_fallback_on_bad_iso(prefix: str) -> None:
    assert format_timestamp_label("not-iso", prefix=prefix, now=NOW) == (
        f"{prefix}: Invalid timestamp"
    )


@pytest.mark.parametrize("iso_string", ["20260808", "2026-W32-5"])
def test_format_timestamp_label_rejects_node_invalid_date_forms(iso_string: str) -> None:
    assert format_timestamp_label(iso_string, prefix="Last backup", now=NOW) == (
        "Last backup: Invalid timestamp"
    )


def test_format_timestamp_label_treats_none_as_epoch_like_node_date() -> None:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    label = format_timestamp_label(None, prefix="Last backup", now=epoch)
    assert label.startswith("Last backup: just now (")
    assert "1970-01-01" in label


def test_format_timestamp_label_clamps_future_timestamps_to_just_now() -> None:
    assert format_timestamp_label(
        "2026-08-08T11:00:00.000Z", prefix="Last backup", now=NOW
    ).startswith("Last backup: just now (")


def test_format_timestamp_label_uses_node_normalized_overflow() -> None:
    now = datetime(2026, 3, 5, tzinfo=timezone.utc)

    assert format_timestamp_label(
        "2026-02-31T24:00:00Z", prefix="Last backup", now=now
    ).startswith("Last backup: 1 day ago (")


def test_format_timestamp_label_handles_year_zero_like_node() -> None:
    label = format_timestamp_label(
        "0000-01-01T00:00:00Z", prefix="Last backup", now=NOW
    )

    assert "Invalid timestamp" not in label
