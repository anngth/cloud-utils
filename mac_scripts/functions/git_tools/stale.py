from datetime import datetime, timezone
import re
from typing import Any, Mapping

_MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
_UTC = timezone.utc
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$"
)

def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    date_only = _DATE_ONLY_RE.fullmatch(text) is not None
    if not date_only and _UTC_ISO_RE.fullmatch(text) is None:
        return None
    if date_only:
        text = f"{text}T00:00:00+00:00"
    elif text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
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
