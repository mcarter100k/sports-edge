"""Time handling. UTC inside the machine; America/Los_Angeles for people.

Every stored timestamp is UTC. Conversion for display happens here only.
"""
from datetime import datetime, timedelta, timezone
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


def wall_time_to_utc(day, hour, tz_name):
    """A wall-clock time on `day` (a date) in the named zone, as an aware UTC datetime."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def pacific_day_start(day):
    """Midnight America/Los_Angeles at the start of `day` (a date), as an aware UTC datetime."""
    return datetime(day.year, day.month, day.day, tzinfo=DISPLAY_TZ).astimezone(timezone.utc)


def pacific_midnight(dt):
    """Start of the Pacific calendar day containing dt, as an aware datetime."""
    return dt.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def pacific_day(now, days_back=0):
    """(start, end) of the Pacific calendar day `days_back` days before the one holding `now`."""
    start = pacific_midnight(now)
    for _ in range(days_back):
        start = pacific_midnight(start - timedelta(hours=1))
    return start, pacific_midnight(start + timedelta(hours=30))
