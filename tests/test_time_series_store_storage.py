from datetime import datetime, timedelta

import numpy as np
import pytest

pytest.importorskip("time_series_store")

from infrasys.exceptions import ISNotStored
from infrasys.time_series_store_storage import TimeSeriesStoreStorage
from infrasys.time_series_models import (
    NonSequentialTimeSeries,
    SingleTimeSeries,
    TimeSeriesStorageType,
)

from .models.simple_system import SimpleBus, SimpleGenerator, SimpleSystem


def make_system(tmp_path) -> tuple[SimpleSystem, SimpleGenerator]:
    system = SimpleSystem(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
    )
    bus = SimpleBus(name="bus", voltage=1.0)
    generator = SimpleGenerator(
        name="generator",
        active_power=1.0,
        rating=1.0,
        bus=bus,
        available=True,
    )
    system.add_components(bus, generator)
    return system, generator


def test_single_time_series_round_trip_and_slice(tmp_path):
    system, generator = make_system(tmp_path)
    initial_timestamp = datetime(2024, 1, 1)
    time_series = SingleTimeSeries.from_array(
        np.arange(24, dtype=np.float32),
        "active_power",
        initial_timestamp,
        timedelta(hours=1),
    )

    system.add_time_series(time_series, generator)

    result = system.get_time_series(
        generator,
        name="active_power",
        start_time=initial_timestamp + timedelta(hours=4),
        length=3,
    )
    assert result.initial_timestamp == initial_timestamp + timedelta(hours=4)
    assert result.data.dtype == np.float64
    np.testing.assert_array_equal(result.data, np.array([4.0, 5.0, 6.0]))


def test_nonsequential_time_series_round_trip(tmp_path):
    system, generator = make_system(tmp_path)
    timestamps = np.array(
        [datetime(2030, 1, 1) + timedelta(minutes=x) for x in (0, 5, 30)],
        dtype=object,
    )
    time_series = NonSequentialTimeSeries.from_array(
        np.array([1.0, 2.0, 3.0]),
        timestamps,
        "events",
    )

    system.add_time_series(time_series, generator)
    result = system.get_time_series(
        generator,
        name="events",
        time_series_type=NonSequentialTimeSeries,
    )

    np.testing.assert_array_equal(result.data, time_series.data)
    np.testing.assert_array_equal(result.timestamps, timestamps)


def test_remove_time_series(tmp_path):
    system, generator = make_system(tmp_path)
    time_series = SingleTimeSeries.from_array(
        np.arange(3),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(time_series, generator)

    system.remove_time_series(generator, name="active_power")

    with pytest.raises(ISNotStored):
        system.get_time_series(generator, name="active_power")


def test_convert_to_and_from_time_series_store_storage(tmp_path):
    system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.MEMORY)
    bus = SimpleBus(name="bus", voltage=1.0)
    generator = SimpleGenerator(
        name="generator",
        active_power=1.0,
        rating=1.0,
        bus=bus,
        available=True,
    )
    system.add_components(bus, generator)
    time_series = SingleTimeSeries.from_array(
        np.arange(6),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(time_series, generator)

    system.convert_storage(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
    )
    assert isinstance(system.time_series.storage, TimeSeriesStoreStorage)
    np.testing.assert_array_equal(
        system.get_time_series(generator, name="active_power").data,
        time_series.data,
    )

    system.convert_storage(time_series_storage_type=TimeSeriesStorageType.MEMORY)
    np.testing.assert_array_equal(
        system.get_time_series(generator, name="active_power").data,
        time_series.data,
    )


def test_serialization_round_trip(tmp_path):
    system, generator = make_system(tmp_path / "storage")
    time_series = SingleTimeSeries.from_array(
        np.arange(6),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(time_series, generator)
    filename = tmp_path / "system.json"
    system.to_json(filename)

    writable = SimpleSystem.from_json(filename)
    writable_generator = writable.get_component(SimpleGenerator, generator.name)
    np.testing.assert_array_equal(
        writable.get_time_series(writable_generator, name="active_power").data,
        time_series.data,
    )

    read_only = SimpleSystem.from_json(filename, time_series_read_only=True)
    read_only_generator = read_only.get_component(SimpleGenerator, generator.name)
    np.testing.assert_array_equal(
        read_only.get_time_series(read_only_generator, name="active_power").data,
        time_series.data,
    )
