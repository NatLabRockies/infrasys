import json
import uuid
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest

from infrasys.exceptions import ISConflictingArguments
from infrasys.quantities import ActivePower
from infrasys.time_series_metadata_store import (
    TimeSeriesMetadataStore,
    _deserialize_time_series_metadata,
)
from infrasys.time_series_models import (
    Deterministic,
    DeterministicMetadata,
    TimeSeriesStorageType,
)
from infrasys.utils.sqlite import create_in_memory_db
from tests.models.simple_system import SimpleGenerator, SimpleSystem

TS_STORAGE_OPTIONS = (
    TimeSeriesStorageType.ARROW,
    TimeSeriesStorageType.HDF5,
)


@pytest.mark.parametrize("storage_type", TS_STORAGE_OPTIONS)
def test_with_deterministic_time_series_quantity(tmp_path, storage_type):
    """Test serialization of DeterministicTimeSeries with a Pint quantity and different storage types."""
    system = SimpleSystem(auto_add_composed_components=True, time_series_storage_type=storage_type)
    gen = SimpleGenerator.example()
    system.add_components(gen)

    initial_time = datetime(year=2020, month=9, day=1)
    resolution = timedelta(hours=1)
    horizon = timedelta(hours=8)
    interval = timedelta(hours=1)
    window_count = 3

    forecast_data = [
        [100.0, 101.0, 101.3, 90.0, 98.0, 87.0, 88.0, 67.0],
        [101.0, 101.3, 99.0, 98.0, 88.9, 88.3, 67.1, 89.4],
        [99.0, 67.0, 89.0, 99.9, 100.0, 101.0, 112.0, 101.3],
    ]

    data = ActivePower(np.array(forecast_data), "watts")
    name = "active_power_forecast"
    ts = Deterministic.from_array(
        data, name, initial_time, resolution, horizon, interval, window_count
    )
    system.add_time_series(ts, gen)

    sys_file = tmp_path / "system.json"
    system.to_json(sys_file)

    system2 = SimpleSystem.from_json(sys_file)
    gen2 = system2.get_component(SimpleGenerator, gen.name)
    ts2 = system2.get_time_series(gen2, name=name)
    assert isinstance(ts, Deterministic)
    assert ts2.resolution == resolution
    assert ts2.initial_timestamp == initial_time


@pytest.mark.parametrize("storage_type", TS_STORAGE_OPTIONS)
def test_with_deterministic_single_time_series_quantity(tmp_path, storage_type):
    """Test serialization of Deterministic created from SingleTimeSeries with a Pint quantity and different storage types."""
    system = SimpleSystem(auto_add_composed_components=True, time_series_storage_type=storage_type)
    gen = SimpleGenerator.example()
    system.add_components(gen)

    initial_timestamp = datetime(year=2020, month=1, day=1)
    name = "active_power"
    horizon = timedelta(hours=8)
    interval = timedelta(hours=1)
    resolution = timedelta(hours=1)

    # Create deterministic data directly from array
    # Create forecast windows manually from the time series data
    horizon_steps = int(horizon / resolution)
    interval_steps = int(interval / resolution)
    total_steps = 8784
    window_count = int((total_steps - horizon_steps) / interval_steps) + 1

    forecast_data = []
    for window_idx in range(window_count):
        start_idx = window_idx * interval_steps
        end_idx = start_idx + horizon_steps
        forecast_data.append(list(range(start_idx, end_idx)))

    ts_deterministic = Deterministic.from_array(
        data=np.array(forecast_data),
        name=name,
        resolution=resolution,
        initial_timestamp=initial_timestamp,
        interval=interval,
        horizon=horizon,
        window_count=window_count,
    )
    system.add_time_series(ts_deterministic, gen)

    sys_file = tmp_path / "system.json"
    system.to_json(sys_file)

    system2 = SimpleSystem.from_json(sys_file)
    gen2 = system2.get_component(SimpleGenerator, gen.name)
    ts2 = system2.get_time_series(gen2, name=name, time_series_type=Deterministic)
    assert isinstance(ts_deterministic, Deterministic)
    assert ts2.horizon == horizon
    assert ts2.initial_timestamp == initial_timestamp


def test_deterministic_metadata_get_range():
    """Test the get_range method of DeterministicMetadata."""
    # Set up the deterministic time series parameters
    initial_time = datetime(year=2020, month=9, day=1)
    resolution = timedelta(hours=1)
    horizon = timedelta(hours=8)
    interval = timedelta(hours=4)
    window_count = 3

    # Create a metadata object for testing
    metadata = DeterministicMetadata(
        name="test_ts",
        initial_timestamp=initial_time,
        resolution=resolution,
        interval=interval,
        horizon=horizon,
        window_count=window_count,
        time_series_uuid=uuid.uuid4(),
        type="Deterministic",
    )

    start_idx, length = metadata.get_range()
    # The total length should be: interval_steps * (window_count - 1) + horizon_steps
    # interval_steps = 4, window_count = 3, horizon_steps = 8
    # So total_steps = 4 * (3 - 1) + 8 = 16
    assert start_idx == 0
    assert length == 16

    start_time = initial_time + timedelta(hours=5)
    start_idx, length_val = metadata.get_range(start_time=start_time)
    assert start_idx == 5
    assert length_val == 11  # 16 - 5 = 11

    start_idx, length_val = metadata.get_range(length=10)
    assert start_idx == 0
    assert length_val == 10

    start_time = initial_time + timedelta(hours=5)
    start_idx, length_val = metadata.get_range(start_time=start_time, length=5)
    assert start_idx == 5
    assert length_val == 5

    # Test 5: error cases
    # Start time too early
    with pytest.raises(ISConflictingArguments):
        metadata.get_range(start_time=initial_time - timedelta(hours=1))

    # Start time too late
    last_valid_time = initial_time + (window_count - 1) * interval + horizon
    with pytest.raises(ISConflictingArguments):
        metadata.get_range(start_time=last_valid_time + timedelta(hours=1))

    # Start time not aligned with resolution
    with pytest.raises(ISConflictingArguments):
        metadata.get_range(start_time=initial_time + timedelta(minutes=30))

    # Length too large
    with pytest.raises(ISConflictingArguments):
        metadata.get_range(start_time=initial_time + timedelta(hours=10), length=10)


def test_deterministic_single_time_series_backwards_compatibility(tmp_path: Any) -> None:
    """Test compatibility for DeterministicSingleTimeSeries type from IS.jl."""
    # Simulate metadata that would come from IS.jl with DeterministicSingleTimeSeries
    # Note: resolution, interval, and horizon are stored as ISO 8601 strings in the DB
    legacy_metadata_dict: dict[str, Any] = {
        "metadata_uuid": str(uuid.uuid4()),
        "time_series_uuid": str(uuid.uuid4()),
        "time_series_type": "DeterministicSingleTimeSeries",
        "name": "test_forecast",
        "initial_timestamp": datetime(2020, 1, 1),
        "resolution": "PT1H",  # ISO 8601 format for 1 hour
        "interval": "PT4H",  # ISO 8601 format for 4 hours
        "horizon": "PT8H",  # ISO 8601 format for 8 hours
        "window_count": 5,
        "features": None,
        "scaling_factor_multiplier": None,
        "units": None,
    }
    metadata = _deserialize_time_series_metadata(legacy_metadata_dict.copy())

    # Verify it was converted to Deterministic
    assert isinstance(metadata, DeterministicMetadata)
    assert metadata.type == "Deterministic"
    assert metadata.name == "test_forecast"
    assert metadata.initial_timestamp == datetime(2020, 1, 1)
    assert metadata.resolution == timedelta(hours=1)
    assert metadata.interval == timedelta(hours=4)
    assert metadata.horizon == timedelta(hours=8)
    assert metadata.window_count == 5

    conn = create_in_memory_db()
    metadata_store = TimeSeriesMetadataStore(conn, initialize=True)
    cursor = conn.cursor()
    owner_uuid = str(uuid.uuid4())

    rows: list[dict[str, Any]] = [
        {
            "time_series_uuid": legacy_metadata_dict["time_series_uuid"],
            "time_series_type": legacy_metadata_dict["time_series_type"],  # Legacy type name
            "initial_timestamp": legacy_metadata_dict["initial_timestamp"].isoformat(),
            "resolution": legacy_metadata_dict["resolution"],
            "horizon": legacy_metadata_dict["horizon"],
            "interval": legacy_metadata_dict["interval"],
            "window_count": legacy_metadata_dict["window_count"],
            "length": None,
            "name": legacy_metadata_dict["name"],
            "owner_uuid": owner_uuid,
            "owner_type": "SimpleGenerator",
            "owner_category": "Component",
            "features": "[]",  # empty features
            "units": legacy_metadata_dict["units"],
            "metadata_uuid": legacy_metadata_dict["metadata_uuid"],
        }
    ]

    metadata_store._insert_rows(rows, cursor)  # type: ignore[arg-type]
    conn.commit()

    metadata_store._load_metadata_into_memory()  # type: ignore[misc]

    loaded_metadata = metadata_store._cache_metadata[metadata.uuid]  # type: ignore[misc]
    assert isinstance(loaded_metadata, DeterministicMetadata)
    assert loaded_metadata.type == "Deterministic"
    assert loaded_metadata.name == "test_forecast"
    assert loaded_metadata.initial_timestamp == datetime(2020, 1, 1)
    assert loaded_metadata.resolution == timedelta(hours=1)
    assert loaded_metadata.interval == timedelta(hours=4)
    assert loaded_metadata.horizon == timedelta(hours=8)
    assert loaded_metadata.window_count == 5


def test_deserialize_metadata_preserves_all_features() -> None:
    """Test that deserialization preserves all feature key/value pairs."""
    features = {"scenario": "high", "model_year": "2030", "weather_year": "2012"}
    # Features are stored as a sorted list of single-key dicts in the DB
    serialized_features = json.dumps([{k: v} for k, v in sorted(features.items())])

    metadata_dict: dict[str, Any] = {
        "metadata_uuid": str(uuid.uuid4()),
        "time_series_uuid": str(uuid.uuid4()),
        "time_series_type": "SingleTimeSeries",
        "name": "active_power",
        "initial_timestamp": datetime(2020, 1, 1),
        "resolution": "PT1H",
        "length": 100,
        "features": serialized_features,
        "scaling_factor_multiplier": None,
        "units": None,
    }
    metadata = _deserialize_time_series_metadata(metadata_dict)
    assert metadata.features == features
