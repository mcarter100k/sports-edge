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


def display_clock(dt) -> str:
    """Pacific clock time for messages: '4:10 PM PT'."""
    la = dt.astimezone(DISPLAY_TZ)
    return la.strftime("%I:%M %p").lstrip("0") + " PT"


def display_day(dt) -> str:
    """Pacific weekday and date for messages: 'Thu Oct 22'."""
    la = dt.astimezone(DISPLAY_TZ)
    return la.strftime("%a %b ") + str(la.day)


def pacific_midnight(dt):
    """Start of the Pacific calendar day containing dt, as an aware datetime."""
    return dt.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def pmset_time(dt) -> str:
    """Local (Pacific) time in the form `pmset schedule` expects: MM/dd/yy HH:mm:ss."""
    return dt.astimezone(DISPLAY_TZ).strftime("%m/%d/%y %H:%M:%S")
