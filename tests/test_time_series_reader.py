from datetime import datetime, timedelta

import numpy as np
import pytest
from infrastore import InvalidParameterError

from infrasys.quantities import ActivePower
from infrasys.time_series_models import Deterministic, SingleTimeSeries, TimeSeriesStorageType

from .models.simple_system import (
    RenewableGenerator,
    SimpleBus,
    SimpleGenerator,
    SimpleSystem,
)

INITIAL_TIMESTAMP = datetime(2024, 1, 1)
RESOLUTION = timedelta(hours=1)
LENGTH = 12


def make_system(tmp_path, count: int = 4, shared: bool = False):
    """Build a system whose generators each carry one "load" series."""
    system = SimpleSystem(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
    )
    bus = SimpleBus(name="bus", voltage=1.0)
    system.add_component(bus)
    generators = [
        SimpleGenerator(name=f"gen{i}", active_power=1.0, rating=1.0, bus=bus, available=True)
        for i in range(count)
    ]
    system.add_components(*generators)

    profile = np.arange(LENGTH, dtype=np.float64)
    expected = {}
    with system.open_time_series_store() as conn:
        for i, generator in enumerate(generators):
            data = profile if shared else profile + i * 100
            time_series = SingleTimeSeries.from_array(data, "load", INITIAL_TIMESTAMP, RESOLUTION)
            system.add_time_series(time_series, generator, context=conn)
            expected[generator.id] = np.asarray(data)
    return system, generators, expected


def test_reader_returns_every_component_at_each_timestamp(tmp_path):
    system, generators, expected = make_system(tmp_path)
    reader = system.build_time_series_reader(RESOLUTION)

    assert sorted(reader.component_ids) == sorted(g.id for g in generators)
    assert reader.timestamps == [INITIAL_TIMESTAMP + i * RESOLUTION for i in range(LENGTH)]

    for step, when in enumerate(reader.timestamps):
        values = reader.read(when)
        assert set(values) == set(expected)
        for component_id, array in expected.items():
            assert values[component_id] == array[step]


def test_reader_matches_get_time_series(tmp_path):
    system, generators, _ = make_system(tmp_path)
    reader = system.build_time_series_reader(RESOLUTION, name="load")

    for step, when in enumerate(reader.timestamps):
        values = reader.read(when)
        for generator in generators:
            series = system.get_time_series(generator, name="load")
            assert values[generator.id] == series.data[step]


def test_reader_read_columns_matches_read(tmp_path):
    system, _, expected = make_system(tmp_path)
    reader = system.build_time_series_reader(RESOLUTION)

    when = reader.timestamps[3]
    from_dict = reader.read(when)
    from_columns = {}
    for component_ids, values in reader.read_columns(when):
        assert len(component_ids) == len(values)
        from_columns.update(zip(component_ids, values.tolist()))
    assert from_dict == from_columns


def test_reader_filters(tmp_path):
    system, generators, _ = make_system(tmp_path)
    bus = system.get_component(SimpleBus, "bus")
    other = RenewableGenerator(name="wind", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_component(other)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.zeros(LENGTH, dtype=np.float64), "load", INITIAL_TIMESTAMP, RESOLUTION
        ),
        other,
        scenario="high",
    )

    by_type = system.build_time_series_reader(RESOLUTION, component_type=RenewableGenerator)
    assert by_type.component_ids == (other.id,)

    by_feature = system.build_time_series_reader(RESOLUTION, scenario="high")
    assert by_feature.component_ids == (other.id,)

    by_glob = system.build_time_series_reader(RESOLUTION, name_glob="lo*")
    assert len(by_glob.component_ids) == len(generators) + 1


def test_reader_raises_when_nothing_matches(tmp_path):
    system, _, _ = make_system(tmp_path)
    # The store refuses to build a reader over an empty match rather than returning one
    # that reads nothing.
    with pytest.raises(InvalidParameterError):
        system.build_time_series_reader(RESOLUTION, name="missing")


def test_reader_rejects_off_grid_timestamp(tmp_path):
    system, _, _ = make_system(tmp_path)
    reader = system.build_time_series_reader(RESOLUTION)
    with pytest.raises(InvalidParameterError):
        reader.read(INITIAL_TIMESTAMP + timedelta(minutes=17))


def test_reader_exposes_units(tmp_path):
    system, _, _ = make_system(tmp_path, count=1)
    bus = system.get_component(SimpleBus, "bus")
    metered = SimpleGenerator(
        name="metered", active_power=1.0, rating=1.0, bus=bus, available=True
    )
    system.add_component(metered)
    system.add_time_series(
        SingleTimeSeries.from_array(
            ActivePower(np.arange(LENGTH, dtype=np.float64), "watts"),
            "load",
            INITIAL_TIMESTAMP,
            RESOLUTION,
        ),
        metered,
    )

    reader = system.build_time_series_reader(RESOLUTION)
    assert reader.units[metered.id] is not None
    assert reader.units[metered.id].units == "watt"
    unitless = [id_ for id_ in reader.component_ids if id_ != metered.id]
    assert all(reader.units[id_] is None for id_ in unitless)


def test_reader_sees_series_staged_in_an_open_batch(tmp_path):
    system, _, expected = make_system(tmp_path)
    bus = system.get_component(SimpleBus, "bus")
    late = SimpleGenerator(name="late", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_component(late)

    with system.open_time_series_store() as conn:
        system.add_time_series(
            SingleTimeSeries.from_array(
                np.full(LENGTH, 7.0), "load", INITIAL_TIMESTAMP, RESOLUTION
            ),
            late,
            context=conn,
        )
        # Building a reader must flush the batch, or the new series would be invisible.
        reader = system.build_time_series_reader(RESOLUTION)
        assert reader.read(INITIAL_TIMESTAMP)[late.id] == 7.0


def test_reader_requires_a_uniform_grid(tmp_path):
    system, _, _ = make_system(tmp_path)
    bus = system.get_component(SimpleBus, "bus")
    offset = SimpleGenerator(name="offset", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_component(offset)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.zeros(LENGTH, dtype=np.float64),
            "load",
            INITIAL_TIMESTAMP + timedelta(days=30),
            RESOLUTION,
        ),
        offset,
    )
    with pytest.raises(InvalidParameterError):
        system.build_time_series_reader(RESOLUTION, name="load")


def test_forecast_reader_matches_get_time_series(tmp_path):
    system, generators, _ = make_system(tmp_path)
    horizon = timedelta(hours=4)
    system.transform_single_time_series(horizon=horizon, interval=RESOLUTION)

    reader = system.build_forecast_reader(RESOLUTION, name="load")
    assert sorted(reader.component_ids) == sorted(g.id for g in generators)

    forecasts = {
        generator.id: system.get_time_series(
            generator, name="load", time_series_type=Deterministic
        )
        for generator in generators
    }
    for window, when in enumerate(reader.timestamps):
        windows = reader.read(when)
        for component_id, forecast in forecasts.items():
            np.testing.assert_array_equal(
                windows[component_id], forecast.data_array[window], err_msg=f"window {window}"
            )


def test_forecast_reader_deduplicates_shared_windows(tmp_path):
    system, generators, _ = make_system(tmp_path, count=6, shared=True)
    system.transform_single_time_series(horizon=timedelta(hours=4), interval=RESOLUTION)
    reader = system.build_forecast_reader(RESOLUTION, name="load")

    # Every generator carries the same profile, so the store keeps exactly one window.
    assert len(reader.component_ids) == len(generators)
    assert reader.num_slots == 1
    assert len(set(reader.slots.values())) == 1
    assert sorted(reader.components_by_slot()[0]) == sorted(g.id for g in generators)

    when = reader.timestamps[1]
    windows = reader.read(when)
    first = windows[generators[0].id]
    for generator in generators[1:]:
        assert windows[generator.id] is first, "one slot should hand back one array object"

    slots = reader.read_slots(when)
    assert len(slots) == 1
    np.testing.assert_array_equal(slots[0], first)


def test_forecast_reader_separates_distinct_windows(tmp_path):
    system, generators, _ = make_system(tmp_path, count=4, shared=False)
    system.transform_single_time_series(horizon=timedelta(hours=4), interval=RESOLUTION)
    reader = system.build_forecast_reader(RESOLUTION, name="load")

    assert reader.num_slots == len(generators)
    assert len(set(reader.slots.values())) == len(generators)
    windows = reader.read(reader.timestamps[0])
    assert len({window.tobytes() for window in windows.values()}) == len(generators)


def test_forecast_reader_rejects_off_grid_timestamp(tmp_path):
    system, _, _ = make_system(tmp_path)
    system.transform_single_time_series(horizon=timedelta(hours=4), interval=RESOLUTION)
    reader = system.build_forecast_reader(RESOLUTION, name="load")
    with pytest.raises(InvalidParameterError):
        reader.read(INITIAL_TIMESTAMP + timedelta(minutes=17))
