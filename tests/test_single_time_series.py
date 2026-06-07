"""Tests for single time-series models."""

from datetime import datetime, timedelta

import numpy as np
import pytest

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
