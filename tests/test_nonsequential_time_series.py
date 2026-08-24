"""Tests for non-sequential time-series models."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from infrasys.normalization import NormalizationMax
from infrasys.quantities import ActivePower
from infrasys.time_series_models import NonSequentialTimeSeries


@pytest.fixture(name="timestamps")
def sample_timestamps():
    "Sample timestamps sequence"
    base_datetime = datetime(year=2020, month=1, day=1)
    return [base_datetime + timedelta(hours=4 * i) for i in range(4)]


@pytest.fixture(name="quantity_data")
def sample_quantity_data():
    "Sample infrasys quantity data"
    return ActivePower(range(4), "kilowatts")


@pytest.fixture(name="data")
def sample_data():
    "Sample data sequence"
    return range(4)


@pytest.fixture(name="variable_name")
def sample_variable_name():
    "Sample variable name"
    return "active_power"


def test_nonsequential_time_series_attributes(data, timestamps, variable_name):
    "Test NOnSequentialTimeseries with Infrasys Quantities as data"
    length = 4
    ts = NonSequentialTimeSeries.from_array(
        data=data,
        name=variable_name,
        timestamps=timestamps,
    )
    assert isinstance(ts, NonSequentialTimeSeries)
    assert ts.length == length
    assert isinstance(ts.data, np.ndarray)
    assert isinstance(ts.timestamps, np.ndarray)


def test_invalid_sequence_length(data, timestamps, variable_name):
    """Check that time series has at least 2 elements."""
    with pytest.raises(ValueError, match="length must be at least 2"):
        NonSequentialTimeSeries.from_array(
            data=[data[0]], name=variable_name, timestamps=[timestamps[0]]
        )


def test_duplicate_timestamps(data, variable_name):
    """Check that time series has unique timestamps"""
    timestamps = [
        datetime(2020, 5, 17),
        datetime(2020, 5, 17),
        datetime(2020, 5, 18),
        datetime(2020, 5, 20),
    ]
    with pytest.raises(ValueError, match="Timestamps must be unique"):
        NonSequentialTimeSeries.from_array(data=data, name=variable_name, timestamps=timestamps)


def test_chronological_timestamps(data, variable_name):
    """Check that time series has unique timestamps"""
    timestamps = [
        datetime(2020, 6, 17),
        datetime(2020, 5, 17),
        datetime(2020, 5, 18),
        datetime(2020, 5, 20),
    ]
    with pytest.raises(ValueError, match="chronological order"):
        NonSequentialTimeSeries.from_array(data=data, name=variable_name, timestamps=timestamps)


def test_nonsequential_time_series_attributes_with_quantity(
    quantity_data, timestamps, variable_name
):
    "Test NonSequentialTimeseries with Infrasys Quantities as data"
    length = 4

    ts = NonSequentialTimeSeries.from_array(
        data=quantity_data,
        name=variable_name,
        timestamps=timestamps,
    )
    assert isinstance(ts, NonSequentialTimeSeries)
    assert ts.length == length
    assert isinstance(ts.data, ActivePower)
    assert isinstance(ts.timestamps, np.ndarray)


def test_normalization(data, timestamps, variable_name):
    "Test normalization approach on sample data for NonSequentialTimeSeries"
    length = 4
    max_val = data[-1]
    ts = NonSequentialTimeSeries.from_array(
        data=data,
        timestamps=timestamps,
        name=variable_name,
        normalization=NormalizationMax(),
    )
    assert isinstance(ts, NonSequentialTimeSeries)
    assert ts.length == length
    for i, val in enumerate(ts.data):
        assert val == data[i] / max_val


def test_normalization_quantity(quantity_data, timestamps, variable_name):
    "Test normalization approach on sample quantity data for NonSequentialTimeSeries"
    length = 4
    max_val = quantity_data.magnitude[-1]
    ts = NonSequentialTimeSeries.from_array(
        data=quantity_data,
        timestamps=timestamps,
        name=variable_name,
        normalization=NormalizationMax(),
    )
    assert isinstance(ts, NonSequentialTimeSeries)
    assert ts.length == length
    for i, val in enumerate(ts.data):
        assert val == quantity_data.magnitude[i] / max_val
