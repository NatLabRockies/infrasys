from datetime import datetime, timedelta
import numpy as np
import pytest
from infrastore import InvalidParameterError

from infrasys import GeographicInfo
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISInvalidParameter,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.quantities import ActivePower
from infrasys.time_series_store_storage import TimeSeriesStoreStorage
from infrasys.time_series_models import (
    Deterministic,
    DeterministicTimeSeriesKey,
    NonSequentialTimeSeries,
    SingleTimeSeries,
    TimeSeriesStorageType,
)

from .models.simple_system import SimpleBus, SimpleGenerator, SimpleSystem


def make_deterministic(name: str = "active_power", units: bool = False) -> Deterministic:
    data = np.arange(12, dtype=np.float64).reshape(3, 4)
    if units:
        data = ActivePower(data, "watts")
    return Deterministic.from_array(
        data,
        name,
        datetime(2024, 1, 1),
        resolution=timedelta(hours=1),
        horizon=timedelta(hours=4),
        interval=timedelta(hours=1),
        window_count=3,
    )


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


def test_time_series_store_is_default():
    system = SimpleSystem()
    assert isinstance(system.time_series.storage, TimeSeriesStoreStorage)


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


@pytest.mark.parametrize(
    "compression_kwargs",
    [
        {"time_series_compression": "none"},
        {"time_series_compression": "deflate", "time_series_compression_level": 9},
        {"time_series_compression": "deflate", "time_series_shuffle": False},
    ],
)
def test_compression_options_flow_from_system(tmp_path, compression_kwargs):
    """Compression kwargs passed to System reach the backend and round-trip."""
    system = SimpleSystem(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
        **compression_kwargs,
    )
    assert isinstance(system.time_series.storage, TimeSeriesStoreStorage)
    bus = SimpleBus(name="bus", voltage=1.0)
    generator = SimpleGenerator(
        name="generator", active_power=1.0, rating=1.0, bus=bus, available=True
    )
    system.add_components(bus, generator)

    time_series = SingleTimeSeries.from_array(
        np.arange(24, dtype=np.float64),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(time_series, generator)
    result = system.get_time_series(generator, name="active_power")
    np.testing.assert_array_equal(result.data, np.arange(24, dtype=np.float64))


def test_invalid_compression_rejected(tmp_path):
    with pytest.raises(InvalidParameterError):
        TimeSeriesStoreStorage.create_with_temp_directory(tmp_path, compression="lz4")


def test_copy_time_series_preserves_metadata_and_shares_array(tmp_path):
    system, source = make_system(tmp_path)
    destination = system.copy_component(source, name="destination", attach=True)
    values = np.arange(5, dtype=np.float64)
    series = SingleTimeSeries.from_array(
        values,
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(series, source, scenario="high")
    store = system.time_series.storage.store
    source_metadata = store.list_metadata(owner_id=source.id, name="active_power")[0]
    array_count = store.num_distinct_arrays()

    system.copy_time_series(destination, source)

    destination_metadata = store.list_metadata(owner_id=destination.id, name="active_power")[0]
    assert destination_metadata["features"] == source_metadata["features"]
    assert destination_metadata["data_hash"] == source_metadata["data_hash"]
    assert destination_metadata["id"] != source_metadata["id"]
    assert store.num_distinct_arrays() == array_count
    copied = system.get_time_series(destination, name="active_power", scenario="high")
    np.testing.assert_array_equal(copied.data, values)


def test_copy_time_series_name_mapping_skips_unmapped_series(tmp_path):
    system, source = make_system(tmp_path)
    destination = system.copy_component(source, name="destination", attach=True)
    start = datetime(2024, 1, 1)
    resolution = timedelta(hours=1)
    system.add_time_series(
        SingleTimeSeries.from_array(np.arange(3), "selected", start, resolution), source
    )
    system.add_time_series(
        SingleTimeSeries.from_array(np.arange(3) + 3, "skipped", start, resolution), source
    )

    system.copy_time_series(destination, source, name_mapping={"selected": "renamed"})

    assert system.has_time_series(destination, name="renamed")
    assert not system.has_time_series(destination, name="selected")
    assert not system.has_time_series(destination, name="skipped")


def test_copy_time_series_rejects_cross_category(tmp_path):
    system, source = make_system(tmp_path)
    attribute = GeographicInfo.example()
    system.add_supplemental_attribute(source, attribute)

    with pytest.raises(ISInvalidParameter, match="same category"):
        system.copy_time_series(attribute, source)


def test_copy_time_series_in_transaction_copies_staged_source(tmp_path):
    system, source = make_system(tmp_path)
    destination = system.copy_component(source, name="destination", attach=True)
    series = SingleTimeSeries.from_array(
        np.arange(3),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )

    with system.time_series_transaction() as transaction:
        transaction.add_time_series(series, source, scenario="high")
        transaction.copy_time_series(destination, source)
        assert transaction.has_time_series(destination, name="active_power", scenario="high")

    assert system.has_time_series(destination, name="active_power", scenario="high")


def test_copy_time_series_rolls_back_all_copies_on_duplicate(tmp_path):
    system, source = make_system(tmp_path)
    destination = system.copy_component(source, name="destination", attach=True)
    start = datetime(2024, 1, 1)
    resolution = timedelta(hours=1)
    system.add_time_series(
        SingleTimeSeries.from_array(np.arange(3), "first", start, resolution), source
    )
    system.add_time_series(
        SingleTimeSeries.from_array(np.arange(3) + 3, "duplicate", start, resolution), source
    )
    system.add_time_series(
        SingleTimeSeries.from_array(np.arange(3) + 6, "duplicate", start, resolution),
        destination,
    )

    with pytest.raises(ISAlreadyAttached):
        system.copy_time_series(destination, source)

    assert not system.has_time_series(destination, name="first")


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


def test_remove_validates_all_owners_before_changing_store(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator)

    with pytest.raises(ISOperationNotAllowed, match="does not have an id"):
        system.remove_time_series(generator, SimpleGenerator.example(), name="active_power")

    assert system.has_time_series(generator, name="active_power")


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


def test_serialize_from_external_store(tmp_path):
    system, generator = make_system(tmp_path / "source")
    system.add_time_series(make_single(), generator)
    system_path = tmp_path / "source.json"
    system.to_json(system_path)

    destination = tmp_path / "external_copy"
    system.time_series.serialize(
        {},
        destination,
        src=system_path.parent / f"{system_path.stem}_time_series",
    )

    from infrastore import Store

    copied_store = Store.open(
        str(destination / TimeSeriesStoreStorage.STORAGE_FILE), read_only=True
    )
    try:
        assert len(copied_store.list_metadata()) == 1
    finally:
        copied_store.close()


@pytest.mark.parametrize("units", [False, True])
def test_deterministic_round_trip(tmp_path, units):
    system, generator = make_system(tmp_path)
    forecast = make_deterministic(units=units)
    key = system.add_time_series(forecast, generator)
    assert isinstance(key, DeterministicTimeSeriesKey)

    result = system.get_time_series(generator, name="active_power", time_series_type=Deterministic)
    assert isinstance(result, Deterministic)
    np.testing.assert_array_equal(result.data_array, forecast.data_array)
    assert result.initial_timestamp == forecast.initial_timestamp
    assert result.resolution == forecast.resolution
    assert result.horizon == forecast.horizon
    assert result.interval == forecast.interval
    assert result.window_count == forecast.window_count
    if units:
        from infrasys.quantities import ActivePower as _AP

        assert isinstance(result.data, _AP)


def test_duplicate_deterministic_time_series_uses_infrasys_error(tmp_path):
    system, generator = make_system(tmp_path)
    forecast = make_deterministic()
    system.add_time_series(forecast, generator)

    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(forecast, generator)


def test_duplicate_detection_compares_the_complete_feature_set(tmp_path):
    system, generator = make_system(tmp_path)
    series = make_single()

    system.add_time_series(series, generator, scenario="high", year=2030)
    system.add_time_series(series, generator, scenario="high")
    system.add_time_series(series, generator)

    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(series, generator, scenario="high", year=2030)
    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(series, generator, scenario="high")
    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(series, generator)


def test_store_rejects_explicit_forecast_when_derived_forecast_exists(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator)
    system.transform_single_time_series(horizon=timedelta(hours=2), interval=timedelta(hours=1))

    with pytest.raises(InvalidParameterError, match="mutually exclusive"):
        system.add_time_series(make_deterministic(), generator)


def test_store_rejects_derived_forecast_when_explicit_forecast_exists(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_deterministic(), generator)
    system.add_time_series(make_single(), generator)

    with pytest.raises(InvalidParameterError, match="mutually exclusive"):
        system.transform_single_time_series(
            horizon=timedelta(hours=2), interval=timedelta(hours=1)
        )


def test_deterministic_keys(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_deterministic(), generator)
    keys = system.list_time_series_keys(generator, time_series_type=Deterministic)
    assert len(keys) == 1
    assert isinstance(keys[0], DeterministicTimeSeriesKey)
    assert keys[0].window_count == 3


def test_deterministic_serialization_round_trip(tmp_path):
    system, generator = make_system(tmp_path / "storage")
    forecast = make_deterministic()
    system.add_time_series(forecast, generator)
    filename = tmp_path / "system.json"
    system.to_json(filename)

    loaded = SimpleSystem.from_json(filename)
    loaded_generator = loaded.get_component(SimpleGenerator, generator.name)
    result = loaded.get_time_series(
        loaded_generator, name="active_power", time_series_type=Deterministic
    )
    np.testing.assert_array_equal(result.data_array, forecast.data_array)
    assert result.window_count == forecast.window_count
    assert result.horizon == forecast.horizon


def test_transform_single_time_series(tmp_path):
    system, generator = make_system(tmp_path)
    single = SingleTimeSeries.from_array(
        np.arange(12, dtype=np.float64),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(single, generator)

    count = system.transform_single_time_series(
        horizon=timedelta(hours=4), interval=timedelta(hours=2)
    )
    assert count == 1

    forecast = system.get_time_series(
        generator, name="active_power", time_series_type=Deterministic
    )
    assert isinstance(forecast, Deterministic)
    assert forecast.data_array.ndim == 2

    # The underlying SingleTimeSeries is still retrievable.
    original = system.get_time_series(
        generator, name="active_power", time_series_type=SingleTimeSeries
    )
    np.testing.assert_array_equal(original.data, single.data)


def test_transform_single_time_series_round_trip(tmp_path):
    system, generator = make_system(tmp_path / "storage")
    single = SingleTimeSeries.from_array(
        np.arange(12, dtype=np.float64),
        "active_power",
        datetime(2024, 1, 1),
        timedelta(hours=1),
    )
    system.add_time_series(single, generator)
    system.transform_single_time_series(horizon=timedelta(hours=4), interval=timedelta(hours=2))
    expected = system.get_time_series(
        generator, name="active_power", time_series_type=Deterministic
    ).data_array

    filename = tmp_path / "system.json"
    system.to_json(filename)
    loaded = SimpleSystem.from_json(filename)
    loaded_generator = loaded.get_component(SimpleGenerator, generator.name)
    forecast = loaded.get_time_series(
        loaded_generator, name="active_power", time_series_type=Deterministic
    )
    np.testing.assert_array_equal(forecast.data_array, expected)


def test_forecast_rejects_slicing(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_deterministic(), generator)
    with pytest.raises(NotImplementedError):
        system.get_time_series(
            generator,
            name="active_power",
            time_series_type=Deterministic,
            start_time=datetime(2024, 1, 1, 1),
        )


# A "perfect forecast" window ``i`` is the slice of the underlying SingleTimeSeries starting at
# ``i * interval_steps`` with length ``horizon_steps`` (resolution is 1 hour in these tests, so the
# step counts equal the hour counts).
@pytest.mark.parametrize(
    "horizon_hours, interval_hours, length, expected_windows",
    [
        (4, 2, 12, 5),  # overlapping windows, interval > resolution
        (3, 1, 10, 8),  # maximum overlap, interval == resolution
        (2, 2, 8, 4),  # non-overlapping, contiguous windows
        (6, 4, 14, 3),  # partial final stride
    ],
)
def test_transform_single_time_series_window_values(
    tmp_path, horizon_hours, interval_hours, length, expected_windows
):
    system, generator = make_system(tmp_path)
    # Use random (non-monotonic) data so a transpose/orientation bug cannot pass by symmetry.
    rng = np.random.default_rng(20240601)
    underlying = rng.random(length)
    single = SingleTimeSeries.from_array(
        underlying, "load", datetime(2024, 1, 1), timedelta(hours=1)
    )
    system.add_time_series(single, generator)

    horizon = timedelta(hours=horizon_hours)
    interval = timedelta(hours=interval_hours)
    count = system.transform_single_time_series(horizon=horizon, interval=interval)
    assert count == 1

    forecast = system.get_time_series(generator, name="load", time_series_type=Deterministic)
    assert forecast.window_count == expected_windows
    assert forecast.data_array.shape == (expected_windows, horizon_hours)
    assert forecast.horizon == horizon
    assert forecast.interval == interval
    assert forecast.resolution == timedelta(hours=1)
    assert forecast.initial_timestamp == datetime(2024, 1, 1)

    # Each forecast window must equal the slice of the underlying array at its offset.
    for window in range(expected_windows):
        start = window * interval_hours
        np.testing.assert_array_equal(
            forecast.data_array[window],
            underlying[start : start + horizon_hours],
            err_msg=f"window {window} mismatch",
        )

    # The original SingleTimeSeries is untouched by the transform.
    np.testing.assert_array_equal(
        system.get_time_series(generator, name="load", time_series_type=SingleTimeSeries).data,
        underlying,
    )


def test_transform_single_time_series_window_values_round_trip(tmp_path):
    system, generator = make_system(tmp_path / "storage")
    rng = np.random.default_rng(7)
    underlying = rng.random(16)
    single = SingleTimeSeries.from_array(
        underlying, "load", datetime(2024, 1, 1), timedelta(hours=1)
    )
    system.add_time_series(single, generator)
    system.transform_single_time_series(horizon=timedelta(hours=5), interval=timedelta(hours=3))

    filename = tmp_path / "system.json"
    system.to_json(filename)
    loaded = SimpleSystem.from_json(filename)
    loaded_generator = loaded.get_component(SimpleGenerator, generator.name)
    forecast = loaded.get_time_series(
        loaded_generator, name="load", time_series_type=Deterministic
    )

    expected_windows = (16 - 5) // 3 + 1
    assert forecast.window_count == expected_windows
    for window in range(expected_windows):
        start = window * 3
        np.testing.assert_array_equal(
            forecast.data_array[window],
            underlying[start : start + 5],
            err_msg=f"window {window} mismatch after round trip",
        )


def test_transform_single_time_series_preserves_units(tmp_path):
    system, generator = make_system(tmp_path)
    underlying = np.arange(8, dtype=np.float64)
    single = SingleTimeSeries.from_array(
        ActivePower(underlying, "watts"), "load", datetime(2024, 1, 1), timedelta(hours=1)
    )
    system.add_time_series(single, generator)
    system.transform_single_time_series(horizon=timedelta(hours=3), interval=timedelta(hours=1))

    forecast = system.get_time_series(generator, name="load", time_series_type=Deterministic)
    assert isinstance(forecast.data, ActivePower)
    assert str(forecast.data.units) == "watt"
    for window in range((8 - 3) // 1 + 1):
        np.testing.assert_array_equal(
            forecast.data_array[window],
            underlying[window : window + 3],
            err_msg=f"window {window} mismatch",
        )


def test_has_time_series_finds_a_transform_derived_forecast(tmp_path):
    """``Deterministic`` covers both stored forecast tags in the existence probe.

    A transform-derived view is tagged ``DeterministicSingleTimeSeries`` in the store, so
    the probe asks for the store's deterministic *family* rather than either tag alone.
    """
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator, scenario="high", year=2030)
    assert not system.has_time_series(generator, time_series_type=Deterministic)

    system.transform_single_time_series(horizon=timedelta(hours=2), interval=timedelta(hours=1))
    assert system.has_time_series(generator, time_series_type=Deterministic)
    assert system.has_time_series(generator, time_series_type=Deterministic, scenario="high")
    assert not system.has_time_series(generator, time_series_type=Deterministic, scenario="low")
    # The static series the view shares is untouched, and narrower filters still apply.
    assert system.has_time_series(generator, time_series_type=SingleTimeSeries)
    assert system.has_time_series(generator, name="active_power", time_series_type=Deterministic)
    assert not system.has_time_series(generator, name="absent", time_series_type=Deterministic)


def test_has_time_series_finds_an_explicit_forecast_by_family(tmp_path):
    """The same probe answers for an explicitly stored ``Deterministic``."""
    system, generator = make_system(tmp_path)
    system.add_time_series(make_deterministic(), generator)

    assert system.has_time_series(generator, time_series_type=Deterministic)
    assert not system.has_time_series(generator, time_series_type=SingleTimeSeries)


def test_has_time_series_uses_store_existence_probe(tmp_path, monkeypatch):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator)
    storage = system.time_series.storage
    store = storage.store
    probes = []

    class StoreProbe:
        def has_any_time_series(self, **filters):
            probes.append(filters)
            return store.has_any_time_series(**filters)

        def list_metadata(self, **filters):
            msg = "existence checks must not materialize metadata rows"
            raise AssertionError(msg)

    monkeypatch.setattr(storage, "_store", StoreProbe())

    assert system.has_time_series(generator, name="active_power")
    assert len(probes) == 1


def test_has_time_series_returns_false_for_unknown_type(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator)

    context = system.time_series.storage.new_context()
    assert not context.has_metadata(generator, time_series_type="UnknownTimeSeries")


def test_has_time_series_matches_feature_subsets(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator, scenario="high", year=2030)

    assert system.has_time_series(generator, scenario="high")
    assert system.has_time_series(generator, year=2030)
    assert system.has_time_series(generator)
    assert not system.has_time_series(generator, scenario="low")
    assert not system.has_time_series(generator, year=2024)


def test_single_window_forecast_with_zero_interval_round_trip(tmp_path):
    """A single-window forecast may carry a zero interval, kept verbatim through a reload."""
    system, generator = make_system(tmp_path / "storage")
    data = np.arange(4, dtype=np.float64).reshape(1, 4)
    forecast = Deterministic.from_array(
        data,
        "active_power",
        datetime(2024, 1, 1),
        resolution=timedelta(hours=1),
        horizon=timedelta(hours=4),
        interval=timedelta(0),
        window_count=1,
    )
    system.add_time_series(forecast, generator)

    result = system.get_time_series(generator, name="active_power", time_series_type=Deterministic)
    assert result.window_count == 1
    assert result.interval == timedelta(0)
    np.testing.assert_array_equal(result.data_array, data)

    filename = tmp_path / "system.json"
    system.to_json(filename)
    loaded = SimpleSystem.from_json(filename)
    reloaded = loaded.get_time_series(
        loaded.get_component(SimpleGenerator, generator.name),
        name="active_power",
        time_series_type=Deterministic,
    )
    assert reloaded.interval == timedelta(0)
    np.testing.assert_array_equal(reloaded.data_array, data)


def test_transform_single_time_series_with_zero_interval(tmp_path):
    """A zero interval asks the store for exactly one window spanning the whole series."""
    system, generator = make_system(tmp_path)
    underlying = np.arange(4, dtype=np.float64)
    single = SingleTimeSeries.from_array(
        underlying, "active_power", datetime(2024, 1, 1), timedelta(hours=1)
    )
    system.add_time_series(single, generator)

    count = system.transform_single_time_series(horizon=timedelta(hours=4), interval=timedelta(0))
    assert count == 1

    forecast = system.get_time_series(
        generator, name="active_power", time_series_type=Deterministic
    )
    assert forecast.window_count == 1
    assert forecast.interval == timedelta(0)
    np.testing.assert_array_equal(forecast.data_array[0], underlying)


def test_time_series_transaction_defers_writes(tmp_path):
    system, generator = make_system(tmp_path)
    storage = system.time_series.storage
    time_series = [
        SingleTimeSeries.from_array(
            np.arange(8, dtype=np.float64), f"load_{i}", datetime(2024, 1, 1), timedelta(hours=1)
        )
        for i in range(3)
    ]

    with system.time_series_transaction() as txn:
        for ts in time_series:
            txn.add_time_series(ts, generator)
        # The context sees its own staged additions, but the store has not been written yet.
        for ts in time_series:
            assert txn.has_time_series(generator, name=ts.name)
        assert storage.store.list_metadata() == []

    assert len(storage.store.list_metadata()) == len(time_series)
    for expected in time_series:
        actual = system.get_time_series(generator, name=expected.name)
        np.testing.assert_array_equal(actual.data, expected.data)


def test_reading_inside_batch_flushes_pending(tmp_path):
    system, generator = make_system(tmp_path)
    expected = SingleTimeSeries.from_array(
        np.arange(8, dtype=np.float64), "load", datetime(2024, 1, 1), timedelta(hours=1)
    )

    with system.time_series_transaction() as txn:
        txn.add_time_series(expected, generator)
        actual = txn.get_time_series(generator, name="load")
        np.testing.assert_array_equal(actual.data, expected.data)
        # A read forces the flush but leaves the batch open for more additions.
        second = SingleTimeSeries.from_array(
            np.arange(8, dtype=np.float64), "load2", datetime(2024, 1, 1), timedelta(hours=1)
        )
        txn.add_time_series(second, generator)

    assert len(system.time_series.storage.store.list_metadata()) == 2


def test_add_time_series_multiple_owners_is_atomic(tmp_path):
    system, generator = make_system(tmp_path)
    other = SimpleGenerator(
        name="generator2",
        active_power=1.0,
        rating=1.0,
        bus=system.get_component(SimpleBus, "bus"),
        available=True,
    )
    system.add_component(other)
    time_series = SingleTimeSeries.from_array(
        np.arange(8, dtype=np.float64), "load", datetime(2024, 1, 1), timedelta(hours=1)
    )
    system.add_time_series(time_series, other)

    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(time_series, generator, other)

    # The duplicate on the second owner must not leave the series attached to the first.
    assert not system.has_time_series(generator, name="load")
    assert system.has_time_series(other, name="load")


def test_list_time_series_matches_per_series_reads(tmp_path):
    system, generator = make_system(tmp_path)
    initial_timestamp = datetime(2024, 1, 1)
    expected = []
    with system.time_series_transaction() as txn:
        for i in range(4):
            time_series = SingleTimeSeries.from_array(
                np.arange(i, i + 8, dtype=np.float64),
                f"load_{i}",
                initial_timestamp,
                timedelta(hours=1),
            )
            txn.add_time_series(time_series, generator)
            expected.append(time_series)

    listed = system.list_time_series(generator)
    assert len(listed) == len(expected)
    by_name = {x.name: x for x in listed}
    for time_series in expected:
        actual = by_name[time_series.name]
        assert isinstance(actual, SingleTimeSeries)
        np.testing.assert_array_equal(actual.data, time_series.data)
        assert actual.initial_timestamp == initial_timestamp

    sliced = {
        x.name: x
        for x in system.list_time_series(
            generator, start_time=initial_timestamp + timedelta(hours=3), length=2
        )
    }
    for time_series in expected:
        actual = sliced[time_series.name]
        assert isinstance(actual, SingleTimeSeries)
        assert actual.initial_timestamp == initial_timestamp + timedelta(hours=3)
        np.testing.assert_array_equal(actual.data, time_series.data[3:5])


def test_list_time_series_reads_forecasts(tmp_path):
    system, generator = make_system(tmp_path)
    expected = make_deterministic()
    system.add_time_series(expected, generator)

    listed = system.list_time_series(generator, time_series_type=Deterministic)
    assert len(listed) == 1
    assert isinstance(listed[0], Deterministic)
    np.testing.assert_array_equal(listed[0].data_array, expected.data_array)
    assert listed[0].window_count == expected.window_count


def test_list_time_series_reads_non_sequential(tmp_path):
    system, generator = make_system(tmp_path)
    timestamps = np.array(
        [datetime(2024, 1, 1) + timedelta(minutes=x) for x in (0, 5, 30)],
        dtype=object,
    )
    expected = NonSequentialTimeSeries.from_array(np.array([1.0, 2.0, 3.0]), timestamps, "load")
    system.add_time_series(expected, generator)

    listed = system.list_time_series(generator, time_series_type=NonSequentialTimeSeries)
    assert len(listed) == 1
    assert isinstance(listed[0], NonSequentialTimeSeries)
    np.testing.assert_array_equal(listed[0].data, expected.data)
    np.testing.assert_array_equal(listed[0].timestamps, timestamps)


def test_system_close_closes_store(tmp_path):
    system, _ = make_system(tmp_path)
    store = system.time_series.storage.store
    system.close()
    with pytest.raises(Exception, match="closed"):
        store.list_metadata()


def make_single(name: str = "active_power") -> SingleTimeSeries:
    return SingleTimeSeries.from_array(
        np.arange(4, dtype=np.float64), name, datetime(2024, 1, 1), timedelta(hours=1)
    )


def test_remove_component_removes_all_time_series_types(tmp_path):
    system, generator = make_system(tmp_path)
    timestamps = np.array(
        [datetime(2024, 1, 1) + timedelta(minutes=x) for x in (0, 5, 30)],
        dtype=object,
    )
    system.add_time_series(make_single(), generator)
    system.add_time_series(make_deterministic("forecast"), generator)
    system.add_time_series(
        NonSequentialTimeSeries.from_array(np.array([1.0, 2.0, 3.0]), timestamps, "events"),
        generator,
    )
    owner_id = generator.id
    store = system.time_series.storage.store

    system.remove_component(generator, cascade_down=False)
    assert not store.list_metadata(owner_id=owner_id)


def test_remove_component_with_feature_subset_series(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator, scenario="a")
    system.add_time_series(make_single(), generator, scenario="a", year=2030)
    owner_id = generator.id
    store = system.time_series.storage.store

    system.remove_component(generator, cascade_down=False)
    assert not store.list_metadata(owner_id=owner_id)


def test_time_series_type_none_matches_all_types(tmp_path):
    system, generator = make_system(tmp_path)
    system.add_time_series(make_single(), generator)
    system.add_time_series(make_deterministic("forecast"), generator)

    assert system.has_time_series(generator, time_series_type=None)
    keys = system.list_time_series_keys(generator, time_series_type=None)
    assert len(keys) == 2
    listed = system.list_time_series(generator, time_series_type=None)
    assert len(listed) == 2

    system.remove_time_series(generator, time_series_type=None)
    assert not system.has_time_series(generator, time_series_type=None)
