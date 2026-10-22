from datetime import datetime, timezone

import pytest

from sports_edge.timeutil import display, iso_z, parse_utc


def test_parse_z_and_offset():
    a = parse_utc("2026-10-23T02:00:00Z")
    b = parse_utc("2026-10-22T19:00:00-07:00")
    assert a == b and a.tzinfo == timezone.utc


def test_naive_is_rejected():
    with pytest.raises(ValueError):
        parse_utc("2026-10-23T02:00:00")
    with pytest.raises(ValueError):
        iso_z(datetime(2026, 10, 23))


def test_empty_is_none():
    assert parse_utc("") is None and parse_utc(None) is None


def test_display_is_los_angeles_with_zone():
    assert display(parse_utc("2026-10-23T02:00:00Z")) == "2026-10-22 19:00 PDT"
    assert display(parse_utc("2026-12-23T02:00:00Z")) == "2026-12-22 18:00 PST"


def test_iso_z_round_trip():
    s = "2026-10-23T02:00:00.123Z"
    assert iso_z(parse_utc(s)) == s
