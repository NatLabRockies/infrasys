"""Test related to arrow storage module."""

from datetime import datetime, timedelta

import numpy as np
import pytest
from dateutil.relativedelta import relativedelta

from infrasys.normalization import NormalizationMax
from infrasys.quantities import ActivePower
from infrasys.time_series_models import SingleTimeSeries


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


def test_from_time_array_infers_calendar_month_resolution():
    initial_timestamp = datetime(2024, 1, 31)
    time_index = [initial_timestamp + relativedelta(months=index) for index in range(12)]
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
    "resolution",
    [relativedelta(days=1), relativedelta(months=0), relativedelta(months=-1)],
)
def test_calendar_resolution_requires_positive_whole_months_or_years(resolution):
    with pytest.raises(ValueError, match="positive whole months or years"):
        SingleTimeSeries.from_array(
            data=[1.0, 2.0],
            name="monthly_demand",
            initial_timestamp=datetime(2024, 1, 1),
            resolution=resolution,
        )


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
