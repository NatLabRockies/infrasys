from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from dateutil.relativedelta import relativedelta

from infrasys.exceptions import ISInvalidParameter
from infrasys.utils.time_utils import (
    advance,
    as_instant,
    from_catalog_timestamp,
    from_iso_8601,
    is_zoneless,
    str_timedelta_to_iso_8601,
    to_iso_8601,
    tzinfo_from_reference,
)

DENVER = ZoneInfo("America/Denver")


def test_to_iso_8601():
    delta = timedelta(minutes=10)

    result = to_iso_8601(delta)
    assert isinstance(result, str)
    assert result == "P0DT10M"

    with pytest.raises(TypeError):
        _ = to_iso_8601("2020")  # type: ignore

    delta = timedelta(microseconds=5.6)
    with pytest.raises(ValueError):
        _ = to_iso_8601(delta)


def test_from_iso_8601():
    delta_str = "P10M"
    result = from_iso_8601(delta_str)
    assert isinstance(result, relativedelta)
    assert result.months == 10

    delta_str = "P0DT0.100S"
    result = from_iso_8601(delta_str)
    assert isinstance(result, timedelta)
    assert result.total_seconds() == 0.1

    delta_str = "P0DT35.0024S"
    with pytest.raises(ValueError):
        _ = from_iso_8601(delta_str)

    delta_str = "WrongString"
    with pytest.raises(ValueError):
        _ = from_iso_8601(delta_str)


def test_duration_round_trip():
    delta = timedelta(minutes=10)
    result_timedelta = to_iso_8601(delta)
    assert isinstance(result_timedelta, str)
    assert result_timedelta == "P0DT10M"

    result_iso8601 = from_iso_8601(result_timedelta)
    assert isinstance(result_iso8601, timedelta)
    assert result_iso8601.total_seconds() / 60 == 10.0

    delta_relative = relativedelta(months=10)
    result_timedelta = to_iso_8601(delta_relative)
    assert isinstance(result_timedelta, str)
    assert result_timedelta == "P10M"

    result_iso8601 = from_iso_8601(result_timedelta)
    assert isinstance(result_iso8601, relativedelta)
    assert result_iso8601.months == 10.0


def test_duration_with_relative_delta():
    delta = relativedelta(months=1)
    result = to_iso_8601(delta)
    assert isinstance(result, str)
    assert result == "P1M"

    delta = relativedelta(years=1)
    result = to_iso_8601(delta)
    assert isinstance(result, str)
    assert result == "P1Y"


def test_str_timedelta_to_iso_8601():
    str_delta = str(timedelta(hours=1))
    result = str_timedelta_to_iso_8601(str_delta)
    assert result
    assert result == "P0DT1H"

    str_delta = str(timedelta(minutes=30))
    result = str_timedelta_to_iso_8601(str_delta)
    assert result
    assert result == "P0DT30M"

    with pytest.raises(ValueError):
        _ = str_timedelta_to_iso_8601("test")


@pytest.mark.parametrize(
    "input_value, result",
    [
        ({"hours": 1}, "P0DT1H"),
        ({"minutes": 30}, "P0DT30M"),
        ({"minutes": 60}, "P0DT1H"),
        ({"weeks": 1}, "P1W"),
        ({"days": 5}, "P5D"),
        ({"days": 7}, "P1W"),
        ({"microseconds": 6_000_00}, "P0DT0.600S"),  # 600 ms
        ({"seconds": 30}, "P0DT30.000S"),
        ({"seconds": 60}, "P0DT1M"),
        ({"microseconds": 6_000_01}, "P0DT0.600S"),  # Validate that we produce milliseconds only
    ],
    ids=[
        "1 Hour",
        "30 Minutes",
        "60 Minutes",
        "1 Week",
        "5 Days",
        "7 Days",
        "600 milliseconds",
        "30 Seconds",
        "60 Seconds",
        "Only milliseconds",
    ],
)
@pytest.mark.parametrize("module", [timedelta, relativedelta], ids=["timedelta", "relativedelta"])
def test_resolution_to_isoformat(module, input_value, result):
    assert to_iso_8601(module(**input_value)) == result


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("utc", timezone.utc),
        (None, timezone.utc),
        ("zoneless", None),
        ("-07:00", timezone(timedelta(hours=-7))),
        ("+05:30", timezone(timedelta(hours=5, minutes=30))),
        ("America/Denver", DENVER),
    ],
)
def test_tzinfo_from_reference(reference, expected):
    assert tzinfo_from_reference(reference) == expected


def test_tzinfo_from_reference_names_an_unknown_zone():
    with pytest.raises(ISInvalidParameter, match="tz database"):
        tzinfo_from_reference("Mars/Olympus_Mons")


@pytest.mark.parametrize(
    ("text", "reference", "expected"),
    [
        ("2024-01-01T00:00:00", "zoneless", datetime(2024, 1, 1)),
        ("2024-01-01T00:00:00+00:00", "utc", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        ("2024-01-01T00:00:00+00:00", None, datetime(2024, 1, 1, tzinfo=timezone.utc)),
        (
            "2024-01-01T07:00:00+00:00",
            "-07:00",
            datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-7))),
        ),
        ("2024-01-01T07:00:00+00:00", "America/Denver", datetime(2024, 1, 1, tzinfo=DENVER)),
    ],
)
def test_from_catalog_timestamp(text, reference, expected):
    parsed = from_catalog_timestamp(text, reference)
    assert parsed == expected
    assert parsed.utcoffset() == expected.utcoffset()


def test_is_zoneless():
    assert is_zoneless(datetime(2024, 1, 1))
    assert not is_zoneless(datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert not is_zoneless(datetime(2024, 1, 1, tzinfo=DENVER))


def test_advance_steps_instants_across_a_transition():
    """Denver skips 02:00 on 2024-03-10, so 24 hours of instants is 25 on the clock."""
    start = datetime(2024, 3, 9, 12, tzinfo=DENVER)
    stepped = advance(start, timedelta(days=1))
    assert stepped == datetime(2024, 3, 10, 13, tzinfo=DENVER)
    assert stepped.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(days=1)
    # Plain addition carries the wall clock across instead, which is the trap: it lands
    # an hour early, 23 hours of instants after the start.
    assert start + timedelta(days=1) == datetime(2024, 3, 10, 12, tzinfo=DENVER)


def test_advance_leaves_a_wall_clock_alone():
    assert advance(datetime(2024, 3, 9), timedelta(days=1)) == datetime(2024, 3, 10)


def test_as_instant_makes_a_zoned_difference_an_instant_difference():
    start = datetime(2024, 3, 9, tzinfo=DENVER)
    later = datetime(2024, 3, 10, 7, tzinfo=DENVER)
    # Sharing a tzinfo, Python subtracts wall clocks and answers 31 hours.
    assert later - start == timedelta(hours=31)
    assert as_instant(later) - as_instant(start) == timedelta(hours=30)
    assert as_instant(datetime(2024, 3, 9)) == datetime(2024, 3, 9)
