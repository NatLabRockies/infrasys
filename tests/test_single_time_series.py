"""Test related to arrow storage module."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from dateutil.relativedelta import relativedelta

from infrasys.exceptions import ISConflictingArguments
from infrasys.normalization import NormalizationMax
from infrasys.quantities import ActivePower
from infrasys.time_series_models import (
    SingleTimeSeries,
    SingleTimeSeriesKey,
    SingleTimeSeriesMetadata,
)
from infrasys.utils.time_utils import infer_time_series_resolution


def test_single_time_series_attributes():
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    length = 8784
    variable_name = "active_power"
    data = range(length)
    ts = SingleTimeSeries.from_array(
        data=data, name=variable_name, initial_timestamp=start, resolution=resolution
    )
    assert ts.length == length
    assert ts.resolution == resolution
    assert ts.initial_timestamp == start
    assert isinstance(ts.data, np.ndarray)
    assert ts.data[-1] == length - 1


def test_from_array_construction():
    """Test SingleTimeSeries.from_array construction."""
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    length = 8784
    data = range(length)
    variable_name = "active_power"
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    assert isinstance(ts, SingleTimeSeries)
    assert ts.length == length
    assert ts.resolution == resolution
    assert ts.initial_timestamp == start
    assert isinstance(ts.data, np.ndarray)
    assert ts.data[-1] == length - 1


def test_invalid_sequence_length():
    """Check that time series has at least 2 elements."""
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    length = 1
    data = range(length)
    variable_name = "active_power"
    with pytest.raises(ValueError, match="length must be at least 2"):
        SingleTimeSeries.from_array(data, variable_name, start, resolution)


def test_from_time_array_constructor():
    """Test SingleTimeSeries.from_time_array construction."""
    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    time_array = [initial_time + timedelta(hours=i) for i in range(length)]
    data = range(length)
    variable_name = "active_power"
    ts = SingleTimeSeries.from_time_array(data, variable_name, time_array)
    assert isinstance(ts, SingleTimeSeries)
    assert ts.length == length
    assert ts.resolution == resolution
    assert ts.initial_timestamp == initial_time
    assert isinstance(ts.data, np.ndarray)
    assert ts.data[-1] == length - 1


def test_from_time_array_rejects_irregular_timestamps():
    timestamps = [datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 4)]

    with pytest.raises(ValueError, match="NonSequentialTimeSeries"):
        SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)


@pytest.mark.parametrize("unit", ["D", "s", "ms", "us", "ns"])
def test_from_time_array_infers_fixed_numpy_resolution(unit):
    timestamps = np.array(["2024-01-01", "2024-01-02", "2024-01-03"], dtype=f"datetime64[{unit}]")

    time_series = SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)
    assert time_series.initial_timestamp == datetime(2024, 1, 1)
    assert time_series.resolution == timedelta(days=1)
    assert np.array_equal(time_series.make_timestamps(), timestamps)


def test_from_time_array_rejects_irregular_numpy_timestamps():
    timestamps = np.array(["2024-01-01", "2024-01-02", "2024-01-04"], dtype="datetime64[ns]")

    with pytest.raises(ValueError, match="NonSequentialTimeSeries"):
        SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)


def test_from_time_array_rejects_irregular_submicrosecond_timestamps():
    timestamps = np.array(
        [
            "2024-01-01T00:00:00.000000000",
            "2024-01-01T01:00:00.000000000",
            "2024-01-01T02:00:00.000000001",
        ],
        dtype="datetime64[ns]",
    )

    with pytest.raises(ValueError):
        SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)


def test_from_time_array_preserves_timezone_aware_pandas_timestamps():
    timestamps = list(
        pd.date_range("2024-03-10 01:00", periods=3, freq="h", tz="America/New_York")
    )

    time_series = SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)
    assert time_series.resolution == timedelta(hours=1)
    assert np.array_equal(
        time_series.make_timestamps(),
        np.array(
            ["2024-03-10T06:00", "2024-03-10T07:00", "2024-03-10T08:00"],
            dtype="datetime64[ns]",
        ),
    )


def test_from_time_array_rejects_pandas_timestamps_finer_than_microseconds():
    timestamps = list(
        pd.date_range("2024-01-01", periods=3, freq="h") + pd.Timedelta(1, unit="ns")
    )

    with pytest.raises(ValueError, match="microsecond"):
        SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)


def test_from_time_array_infers_fixed_interval_across_daylight_saving():
    zone = ZoneInfo("America/New_York")
    timestamps = [
        datetime(2024, 3, 10, 1, tzinfo=zone),
        datetime(2024, 3, 10, 3, tzinfo=zone),
        datetime(2024, 3, 10, 4, tzinfo=zone),
    ]

    time_series = SingleTimeSeries.from_time_array([1, 2, 3], "demand", timestamps)
    assert time_series.resolution == timedelta(hours=1)
    assert np.array_equal(
        time_series.make_timestamps(),
        np.array(
            ["2024-03-10T06:00", "2024-03-10T07:00", "2024-03-10T08:00"], dtype="datetime64[ns]"
        ),
    )


def test_from_time_array_infers_fixed_grid_after_calendar_candidate_does_not_match():
    timestamps = [datetime(2024, 7, 1) + index * timedelta(days=31) for index in range(4)]

    time_series = SingleTimeSeries.from_time_array([1, 2, 3, 4], "demand", timestamps)
    assert time_series.resolution == timedelta(days=31)
    assert np.array_equal(
        time_series.make_timestamps(), np.array(timestamps, dtype="datetime64[ns]")
    )


def test_from_time_array_infers_thirteen_month_resolution():
    time_index = [datetime(2023, 1, 31), datetime(2024, 2, 29), datetime(2025, 3, 31)]
    assert infer_time_series_resolution(time_index) == relativedelta(months=13)

    time_series = SingleTimeSeries.from_time_array([1, 2, 3], "demand", time_index)
    assert time_series.resolution == relativedelta(months=13)
    assert time_series.model_dump(mode="json", include={"resolution"}) == {"resolution": "P13M"}
    assert np.array_equal(
        time_series.make_timestamps(), np.array(time_index, dtype="datetime64[ns]")
    )

    irregular_index = [*time_index[:2], datetime(2025, 3, 30)]
    with pytest.raises(ValueError, match="NonSequentialTimeSeries"):
        infer_time_series_resolution(irregular_index)


def test_from_time_array_infers_calendar_month_resolution():
    initial_timestamp = datetime(2024, 1, 31)
    time_index = [initial_timestamp + relativedelta(months=index) for index in range(12)]
    assert infer_time_series_resolution(time_index) == relativedelta(months=1)
    assert infer_time_series_resolution(time_index[:2]) == timedelta(days=29)
    with pytest.raises(ValueError, match="NonSequentialTimeSeries"):
        infer_time_series_resolution(
            [datetime(2024, 1, 31), datetime(2024, 2, 29), datetime(2024, 3, 30)]
        )

    time_series = SingleTimeSeries.from_time_array(
        data=range(12),
        name="monthly_demand",
        time_index=time_index,
    )

    assert time_series.resolution == relativedelta(months=1)
    assert np.array_equal(
        time_series.make_timestamps(),
        np.array(time_index, dtype="datetime64[ns]"),
    )

    numpy_time_series = SingleTimeSeries.from_time_array(
        data=range(12),
        name="monthly_demand",
        time_index=np.array(time_index, dtype="datetime64[ns]"),
    )
    assert numpy_time_series.resolution == relativedelta(months=1)
    assert np.array_equal(numpy_time_series.make_timestamps(), time_series.make_timestamps())


def test_with_quantity():
    """Test SingleTimeSeries with a Pint quantity."""
    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    time_array = [initial_time + timedelta(hours=i) for i in range(length)]
    data = ActivePower(range(length), "watts")
    variable_name = "active_power"
    ts = SingleTimeSeries.from_time_array(data, variable_name, time_array)
    assert isinstance(ts, SingleTimeSeries)
    assert ts.length == length
    assert ts.resolution == resolution
    assert ts.initial_timestamp == initial_time
    assert isinstance(ts.data, ActivePower)
    assert ts.data[-1].magnitude == length - 1


def test_calendar_month_resolution_generates_anchored_timestamps():
    initial_timestamp = datetime(2024, 1, 31)
    resolution = relativedelta(months=1)
    time_series = SingleTimeSeries.from_array(
        data=range(12),
        name="monthly_demand",
        initial_timestamp=initial_timestamp,
        resolution=resolution,
    )

    expected_timestamps = np.array(
        [
            datetime(2024, 1, 31),
            datetime(2024, 2, 29),
            datetime(2024, 3, 31),
            datetime(2024, 4, 30),
            datetime(2024, 5, 31),
            datetime(2024, 6, 30),
            datetime(2024, 7, 31),
            datetime(2024, 8, 31),
            datetime(2024, 9, 30),
            datetime(2024, 10, 31),
            datetime(2024, 11, 30),
            datetime(2024, 12, 31),
        ],
        dtype="datetime64[ns]",
    )
    assert time_series.resolution == resolution
    assert np.array_equal(time_series.make_timestamps(), expected_timestamps)


@pytest.mark.parametrize(
    "resolution, expected",
    [
        (relativedelta(months=3), relativedelta(months=3)),
        (relativedelta(years=2), relativedelta(years=2)),
        (relativedelta(months=13), relativedelta(months=13)),
        (relativedelta(years=1, months=1), relativedelta(months=13)),
        (relativedelta(years=1, months=-1), relativedelta(months=11)),
        ("P13M", relativedelta(months=13)),
        ("P1Y1M", relativedelta(months=13)),
        ("P3M", relativedelta(months=3)),
        ("P2Y", relativedelta(years=2)),
        ("P12M", relativedelta(years=1)),
        (timedelta(days=1, hours=2), timedelta(days=1, hours=2)),
    ],
)
def test_calendar_month_counts_and_fixed_resolutions_are_supported(resolution, expected):
    time_series = SingleTimeSeries.from_array(
        data=[1, 2],
        name="demand",
        initial_timestamp=datetime(2024, 1, 1),
        resolution=resolution,
    )
    assert time_series.resolution == expected


@pytest.mark.parametrize(
    "resolution",
    [
        relativedelta(days=1),
        relativedelta(months=0),
        relativedelta(months=-1),
        relativedelta(years=-1, months=1),
        relativedelta(months=1, days=1),
        relativedelta(years=1, day=1),
        "P1M1D",
    ],
)
def test_calendar_resolution_requires_positive_whole_months_or_years(resolution):
    with pytest.raises(ValueError, match="positive whole months or years"):
        SingleTimeSeries.from_array(
            data=[1.0, 2.0],
            name="monthly_demand",
            initial_timestamp=datetime(2024, 1, 1),
            resolution=resolution,
        )


def test_calendar_resolution_is_validated_on_assignment():
    time_series = SingleTimeSeries.from_array(
        [1, 2], "demand", datetime(2024, 1, 1), relativedelta(months=1)
    )

    with pytest.raises(ValueError, match="positive whole months or years"):
        time_series.resolution = relativedelta(months=1, days=1)
    assert time_series.resolution == relativedelta(months=1)


def test_calendar_month_count_normalizes_opposite_signed_year_and_month_fields():
    time_series = SingleTimeSeries.from_array(
        [1, 2], "demand", datetime(2024, 1, 1), relativedelta(years=1, months=-1)
    )

    assert time_series.resolution == relativedelta(months=11)
    assert time_series.model_dump(mode="json", include={"resolution"}) == {"resolution": "P11M"}


@pytest.mark.parametrize("resolution", [relativedelta(years=1, months=1), "P1Y1M", "P13M"])
def test_metadata_and_keys_round_trip_calendar_month_counts(resolution):
    shared_fields = {
        "name": "monthly_demand",
        "initial_timestamp": datetime(2024, 1, 31),
        "resolution": resolution,
    }
    metadata = SingleTimeSeriesMetadata(**shared_fields, time_series_uuid=uuid4(), length=2)
    key = SingleTimeSeriesKey(**shared_fields, time_series_type=SingleTimeSeries, length=2)

    assert metadata.resolution == relativedelta(months=13)
    assert key.resolution == relativedelta(months=13)
    assert metadata.model_dump(mode="json", include={"resolution"}) == {"resolution": "P13M"}
    assert key.model_dump(mode="json", include={"resolution"}) == {"resolution": "P13M"}
    restored = SingleTimeSeriesMetadata.model_validate_json(metadata.model_dump_json())
    assert restored.resolution == relativedelta(months=13)


def test_calendar_slice_rejects_timezone_reanchoring_that_changes_instants():
    metadata = SingleTimeSeriesMetadata(
        name="demand",
        time_series_uuid=uuid4(),
        initial_timestamp=datetime(2024, 1, 15, tzinfo=ZoneInfo("America/New_York")),
        resolution=relativedelta(months=1),
        length=3,
    )

    with pytest.raises(ISConflictingArguments, match="cannot preserve.*timestamps"):
        metadata.get_range(start_time=datetime(2024, 2, 15, 5, tzinfo=timezone.utc), length=2)


@pytest.mark.parametrize("resolution", [relativedelta(months=1, days=1), "P1M1D"])
def test_metadata_and_keys_reject_mixed_calendar_and_fixed_units(resolution):
    shared_fields = {
        "name": "monthly_demand",
        "initial_timestamp": datetime(2024, 1, 31),
        "resolution": resolution,
    }

    with pytest.raises(ValueError, match="positive whole months or years"):
        SingleTimeSeriesMetadata(**shared_fields, time_series_uuid=uuid4(), length=2)
    with pytest.raises(ValueError, match="positive whole months or years"):
        SingleTimeSeriesKey(**shared_fields, time_series_type=SingleTimeSeries, length=2)


def test_normalization():
    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    time_array = [initial_time + timedelta(hours=i) for i in range(length)]
    data = [1.1, 2.2, 3.3, 4.5, 5.5]
    max_val = data[-1]
    variable_name = "active_power"
    ts = SingleTimeSeries.from_time_array(
        data, variable_name, time_array, normalization=NormalizationMax()
    )
    assert isinstance(ts, SingleTimeSeries)
    assert ts.length == len(data)
    for i, val in enumerate(ts.data):
        assert val == data[i] / max_val
