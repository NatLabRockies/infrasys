from datetime import datetime, timedelta

import numpy as np
import pytest

from infrasys.exceptions import ISAlreadyAttached
from infrasys.in_memory_time_series_storage import InMemoryTimeSeriesStorage
from infrasys.time_series_models import (
    NonSequentialTimeSeries,
    SingleTimeSeries,
    TimeSeriesStorageType,
)
from infrasys.time_series_store_storage import TimeSeriesStoreStorage

from .models.simple_system import SimpleGenerator, SimpleSystem


@pytest.mark.parametrize(
    "original_type,new_type,original_class,new_class",
    [
        (
            TimeSeriesStorageType.MEMORY,
            TimeSeriesStorageType.TIME_SERIES_STORE,
            InMemoryTimeSeriesStorage,
            TimeSeriesStoreStorage,
        ),
        (
            TimeSeriesStorageType.TIME_SERIES_STORE,
            TimeSeriesStorageType.MEMORY,
            TimeSeriesStoreStorage,
            InMemoryTimeSeriesStorage,
        ),
    ],
)
def test_convert_storage_single_time_series(
    tmp_path,
    original_type,
    new_type,
    original_class,
    new_class,
):
    generator = SimpleGenerator.example()
    system = SimpleSystem(
        auto_add_composed_components=True,
        time_series_storage_type=original_type,
    )
    system.add_components(generator)
    time_series = SingleTimeSeries(
        data=np.arange(24),
        resolution=timedelta(hours=1),
        initial_timestamp=datetime(2020, 1, 1),
        name="load",
    )
    system.add_time_series(time_series, generator)
    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(time_series, generator)

    assert isinstance(system.time_series.storage, original_class)
    system.convert_storage(
        time_series_storage_type=new_type,
        time_series_directory=tmp_path,
    )

    assert isinstance(system.time_series.storage, new_class)
    result = system.get_time_series(generator, name="load")
    np.testing.assert_array_equal(result.data_array, time_series.data_array)


@pytest.mark.parametrize(
    "original_type,new_type,original_class,new_class",
    [
        (
            TimeSeriesStorageType.MEMORY,
            TimeSeriesStorageType.TIME_SERIES_STORE,
            InMemoryTimeSeriesStorage,
            TimeSeriesStoreStorage,
        ),
        (
            TimeSeriesStorageType.TIME_SERIES_STORE,
            TimeSeriesStorageType.MEMORY,
            TimeSeriesStoreStorage,
            InMemoryTimeSeriesStorage,
        ),
    ],
)
def test_convert_storage_nonsequential_time_series(
    tmp_path,
    original_type,
    new_type,
    original_class,
    new_class,
):
    generator = SimpleGenerator.example()
    system = SimpleSystem(
        auto_add_composed_components=True,
        time_series_storage_type=original_type,
    )
    system.add_components(generator)
    timestamps = np.array(
        [datetime(2030, 1, 1) + timedelta(seconds=5 * i) for i in range(24)],
    )
    time_series = NonSequentialTimeSeries(
        data=np.arange(24),
        timestamps=timestamps,
        name="load",
    )
    system.add_time_series(time_series, generator)

    assert isinstance(system.time_series.storage, original_class)
    system.convert_storage(
        time_series_storage_type=new_type,
        time_series_directory=tmp_path,
    )

    assert isinstance(system.time_series.storage, new_class)
    result = system.get_time_series(
        generator,
        time_series_type=NonSequentialTimeSeries,
        name="load",
    )
    np.testing.assert_array_equal(result.data_array, time_series.data_array)
    np.testing.assert_array_equal(result.timestamps, time_series.timestamps)
