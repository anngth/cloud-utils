from datetime import datetime, timezone
import re
import time

_MILLISECONDS_PER_SECOND = 1000
_MILLISECONDS_PER_MINUTE = 60 * _MILLISECONDS_PER_SECOND
_MILLISECONDS_PER_HOUR = 60 * _MILLISECONDS_PER_MINUTE
_MILLISECONDS_PER_DAY = 24 * _MILLISECONDS_PER_HOUR
_UTC = timezone.utc
_MAX_DATE_MILLISECONDS = 8_640_000_000_000_000
_JS_ISO_RE = re.compile(
    r"^(?P<year>\d{4}|[+-]\d{6})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2})"
    r"(?:[Tt](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d+))?)?(?P<zone>[Zz]|[+-]\d{2}:?\d{2})?)?)?)?$"
)

def _days_from_civil(year: int, month: int, day: int) -> int:
    adjusted_year = year - (month <= 2)
    era = adjusted_year // 400
    year_of_era = adjusted_year - era * 400
    month_prime = month + (-3 if month > 2 else 9)
    day_of_year = (153 * month_prime + 2) // 5 + day - 1
    day_of_era = year_of_era * 365 + year_of_era // 4 - year_of_era // 100 + day_of_year
    return era * 146097 + day_of_era - 719468

def _civil_milliseconds(parts: tuple[int, ...], millis: int = 0) -> int:
    year, month, day, hour, minute, second = parts
    return (
        _days_from_civil(year, month, day) * _MILLISECONDS_PER_DAY
        + hour * _MILLISECONDS_PER_HOUR
        + minute * _MILLISECONDS_PER_MINUTE
        + second * _MILLISECONDS_PER_SECOND
        + millis
    )

def _local_epoch_milliseconds(parts: tuple[int, ...], millis: int) -> int | None:
    try:
        return int(time.mktime((*parts, 0, 0, -1))) * 1000 + millis
    except (OverflowError, OSError, ValueError):
        naive = _civil_milliseconds(parts, millis)
        candidate = naive
        try:
            for _ in range(4):
                local, utc = time.localtime(candidate / 1000), time.gmtime(candidate / 1000)
                offset = _civil_milliseconds(local[:6]) - _civil_milliseconds(utc[:6])
                revised = naive - offset
                if revised == candidate:
                    break
                candidate = revised
            return candidate
        except (OverflowError, OSError, ValueError):
            return None

def parse_js_timestamp(value: object) -> int | None:
    if value is None:
        return 0
    if not isinstance(value, str):
        return None
    match = _JS_ISO_RE.fullmatch(value)
    if match is None or match["year"] == "-000000":
        return None

    year = int(match["year"])
    month = int(match["month"] or 1)
    day = int(match["day"] or 1)
    hour = int(match["hour"] or 0)
    minute = int(match["minute"] or 0)
    second = int(match["second"] or 0)
    fraction = match["fraction"] or ""
    millis = int((fraction[:3] + "000")[:3])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    if hour > 24 or minute > 59 or second > 59:
        return None
    if hour == 24 and (minute or second or any(digit != "0" for digit in fraction)):
        return None

    parts = (year, month, day, hour, minute, second)
    has_time = match["hour"] is not None
    zone = match["zone"]
    if has_time and zone is None:
        parsed = _local_epoch_milliseconds(parts, millis)
        return parsed if parsed is not None and abs(parsed) <= _MAX_DATE_MILLISECONDS else None

    parsed = _civil_milliseconds(parts, millis)
    if zone and zone not in {"Z", "z"}:
        offset = zone[1:].replace(":", "")
        offset_hour, offset_minute = int(offset[:2]), int(offset[2:])
        if offset_hour > 23 or offset_minute > 59:
            return None
        direction = 1 if zone[0] == "+" else -1
        parsed -= direction * (
            offset_hour * _MILLISECONDS_PER_HOUR
            + offset_minute * _MILLISECONDS_PER_MINUTE
        )
    return parsed if abs(parsed) <= _MAX_DATE_MILLISECONDS else None

def _parse_timestamp(value: str | None) -> int | None:
    return parse_js_timestamp(value)

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
    then_ms = _parse_timestamp(iso_string)
    if then_ms is None:
        return f"{prefix}: Invalid timestamp"
    if now is None:
        now = datetime.now(tz=_UTC)
    elapsed = max(0, _epoch_milliseconds(now) - then_ms)
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

    try:
        local = time.localtime(then_ms / 1000)
    except (OverflowError, OSError, ValueError):
        return f"{prefix}: Invalid timestamp"
    local_label = (
        f"{local.tm_year}-{local.tm_mon:02d}-{local.tm_mday:02d} "
        f"{local.tm_hour:02d}:{local.tm_min:02d}"
    )
    return f"{prefix}: {relative} ({local_label})"

def format_last_backup_label(
    iso_string: str | None, now: datetime | None = None
) -> str:
    return format_timestamp_label(iso_string, prefix="Last backup", now=now)

def format_last_checked_label(
    iso_string: str | None, now: datetime | None = None
) -> str:
    return format_timestamp_label(iso_string, prefix="Last checked", now=now)
