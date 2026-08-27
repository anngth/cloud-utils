from datetime import datetime, timezone

import pytest

from git_tools.stale import is_stale_repo


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def test_null_and_missing_last_checked_are_stale() -> None:
    assert is_stale_repo({"lastCheckedAt": None}, now=NOW)
    assert is_stale_repo({}, now=NOW)


def test_recent_last_checked_is_not_stale() -> None:
    assert not is_stale_repo({"lastCheckedAt": "2026-08-07T12:00:00.000Z"}, now=NOW)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-08-01T11:59:59.999Z", True),
        ("2026-08-01T12:00:00.000Z", False),
    ],
)
def test_stale_uses_strict_seven_day_millisecond_boundary(
    timestamp: str, expected: bool
) -> None:
    assert is_stale_repo({"lastCheckedAt": timestamp}, now=NOW, days=7) is expected


def test_stale_honors_custom_days_threshold() -> None:
    entry = {"lastCheckedAt": "2026-08-07T11:00:00.000Z"}
    assert is_stale_repo(entry, now=NOW, days=1)
    assert not is_stale_repo(entry, now=NOW, days=2)


def test_invalid_last_checked_is_stale() -> None:
    assert is_stale_repo({"lastCheckedAt": "not-a-date"}, now=NOW)


@pytest.mark.parametrize("timestamp", ["20260808", "2026-W32-5"])
def test_node_invalid_compact_and_week_timestamps_are_stale(timestamp: str) -> None:
    assert is_stale_repo({"lastCheckedAt": timestamp}, now=NOW)


def test_node_normalized_config_timestamp_does_not_trigger_unintended_backup() -> None:
    now = datetime(2026, 3, 5, tzinfo=timezone.utc)

    assert not is_stale_repo(
        {"lastCheckedAt": "2026-02-31T24:00:00Z"}, now=now, days=7
    )


def test_datetime_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        is_stale_repo({}, now=datetime(2026, 8, 8, 12))
