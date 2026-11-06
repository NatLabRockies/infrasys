from datetime import timedelta

import pytest
from dateutil.relativedelta import relativedelta

from infrasys.utils.time_utils import from_iso_8601, str_timedelta_to_iso_8601, to_iso_8601


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

    assert from_iso_8601("PT1H") == timedelta(hours=1)

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
    delta = relativedelta(years=1, months=1)
    assert to_iso_8601(delta) == "P13M"

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
