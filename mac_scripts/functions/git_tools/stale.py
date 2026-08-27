"""Pure stale-repository policy helper."""

from datetime import datetime, timezone
from typing import Any, Mapping


_MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
_UTC = timezone.utc


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(_UTC)
    delta = utc_value - datetime(1970, 1, 1, tzinfo=_UTC)
    return delta.days * _MILLISECONDS_PER_DAY + delta.seconds * 1000 + delta.microseconds // 1000


def is_stale_repo(
    entry: Mapping[str, Any], *, now: datetime | None = None, days: float = 7
) -> bool:
    """Whether ``lastCheckedAt`` is absent, invalid, or strictly too old."""

    if now is None:
        now = datetime.now(tz=_UTC)
    # Validate the caller-supplied clock even when the entry has no timestamp;
    # all datetime values accepted by this module are required to be aware.
    now_ms = _epoch_milliseconds(now)
    last_checked = entry.get("lastCheckedAt")
    if last_checked is None:
        return True
    then = _parse_timestamp(last_checked)
    if then is None:
        return True
    threshold = days * _MILLISECONDS_PER_DAY
    return now_ms - _epoch_milliseconds(then) > threshold
