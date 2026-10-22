"""Time handling. UTC inside the machine; America/Los_Angeles for people.

Every stored timestamp is UTC. Conversion for display happens here only.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/Los_Angeles")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value):
    """Parse an ISO-8601 string to an aware UTC datetime. None/'' -> None.

    A string with no offset is rejected rather than assumed to be UTC.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime: {value!r}")
        return value.astimezone(timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without an offset: {value!r}")
    return dt.astimezone(timezone.utc)


def iso_z(dt) -> str:
    """UTC ISO-8601 with a Z suffix. None -> ''."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def display(dt) -> str:
    """Human-readable Los Angeles time with the zone shown."""
    if dt is None:
        return "never"
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {dt!r}")
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M %Z")


def display_date(dt) -> str:
    """The Los Angeles calendar date, used for day-log file names."""
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
