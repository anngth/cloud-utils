"""Pure timestamp labels used by the ``gt`` UI."""

from datetime import datetime, timezone


_MILLISECONDS_PER_SECOND = 1000
_MILLISECONDS_PER_MINUTE = 60 * _MILLISECONDS_PER_SECOND
_MILLISECONDS_PER_HOUR = 60 * _MILLISECONDS_PER_MINUTE
_MILLISECONDS_PER_DAY = 24 * _MILLISECONDS_PER_HOUR
_UTC = timezone.utc


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    date_only = len(text) == 10 and text[4] == "-" and text[7] == "-"
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if date_only:
        parsed = parsed.replace(tzinfo=_UTC)
    elif parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    utc_value = value.astimezone(_UTC)
    delta = utc_value - datetime(1970, 1, 1, tzinfo=_UTC)
    return delta.days * _MILLISECONDS_PER_DAY + delta.seconds * 1000 + delta.microseconds // 1000


def format_timestamp_label(
    iso_string: str | None,
    *,
    prefix: str = "Last backup",
    now: datetime | None = None,
) -> str:
    then = _parse_timestamp(iso_string)
    if then is None:
        return f"{prefix}: Invalid timestamp"
    if now is None:
        now = datetime.now(tz=_UTC)
    elapsed = max(0, _epoch_milliseconds(now) - _epoch_milliseconds(then))
    seconds = elapsed // _MILLISECONDS_PER_SECOND
    if seconds < 60:
        relative = "just now"
    elif seconds < 3600:
        count = seconds // 60
        relative = f"{count} minute ago" if count == 1 else f"{count} minutes ago"
    elif seconds < 86400:
        count = seconds // 3600
        relative = f"{count} hour ago" if count == 1 else f"{count} hours ago"
    else:
        count = seconds // 86400
        relative = f"{count} day ago" if count == 1 else f"{count} days ago"

    local = then.astimezone()
    local_label = local.strftime("%Y-%m-%d %H:%M")
    return f"{prefix}: {relative} ({local_label})"


def format_last_backup_label(
    iso_string: str | None, now: datetime | None = None
) -> str:
    return format_timestamp_label(iso_string, prefix="Last backup", now=now)


def format_last_checked_label(
    iso_string: str | None, now: datetime | None = None
) -> str:
    return format_timestamp_label(iso_string, prefix="Last checked", now=now)
