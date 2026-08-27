from datetime import datetime, timezone

import pytest

from git_tools.last_backup import (
    format_last_backup_label,
    format_last_checked_label,
    format_timestamp_label,
)


NOW = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


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


def test_format_timestamp_label_clamps_future_timestamps_to_just_now() -> None:
    assert format_timestamp_label(
        "2026-08-08T11:00:00.000Z", prefix="Last backup", now=NOW
    ).startswith("Last backup: just now (")
