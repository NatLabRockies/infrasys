import itertools
from datetime import datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest

from infrasys import TIME_SERIES_ASSOCIATIONS_TABLE, Component, Location, SingleTimeSeries
from infrasys.arrow_storage import ArrowTimeSeriesStorage
from infrasys.chronify_time_series_storage import ChronifyTimeSeriesStorage
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISConflictingArguments,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.quantities import ActivePower
from infrasys.time_series_models import TimeSeriesKey, TimeSeriesStorageType
from infrasys.utils.time_utils import to_iso_8601

from .models.simple_system import (
    GeneratorBase,
    RenewableGenerator,
    SimpleBus,
    SimpleGenerator,
    SimpleSubsystem,
    SimpleSystem,
)


def test_system():
    system = SimpleSystem()
    geo = Location(x=1.0, y=2.0)
    bus = SimpleBus(name="test-bus", voltage=1.1, coordinates=geo)
    gen = SimpleGenerator(name="test-gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    subsystem = SimpleSubsystem(name="test-subsystem", generators=[gen])
    system.add_components(geo, bus, gen, subsystem)
    assert system.add_components() is None  # type: ignore

    gen2 = system.get_component(SimpleGenerator, "test-gen")
    assert gen2 is gen
    assert gen2.bus is bus

    with pytest.raises(ISNotStored):
        system.get_component(SimpleGenerator, "not-present")


def test_system_auto_add_composed_components():
    system = SimpleSystem(auto_add_composed_components=False)
    assert not system.auto_add_composed_components
    geo = Location(x=1.0, y=2.0)
    bus = SimpleBus(name="test-bus", voltage=1.1, coordinates=geo)

    with pytest.raises(ISOperationNotAllowed):
        system.add_component(bus)

    system.auto_add_composed_components = True
    assert system.auto_add_composed_components
    system.add_component(bus)
    assert len(list(system.get_components(Location))) == 1


def test_system_auto_add_composed_components_list():
    system = SimpleSystem(auto_add_composed_components=False)
    assert not system.auto_add_composed_components
    subsystem = SimpleSubsystem.example()
    with pytest.raises(ISOperationNotAllowed):
        system.add_component(subsystem)
    system.auto_add_composed_components = True
    assert system.auto_add_composed_components
    system.add_component(subsystem)


def test_get_components(simple_system: SimpleSystem):
    system = simple_system
    initial_count = 4
    assert len(list(system.get_components(Component))) == initial_count
    system.auto_add_composed_components = True
    for _ in range(5):
        gen = RenewableGenerator.example()
        system.add_component(gen)
    all_components = list(system.get_components(Component))
    # 5 generators, each includes a bus and location
    assert len(all_components) == initial_count + 5 * 3
    generators = list(
        system.get_components(GeneratorBase, filter_func=lambda x: x.name == "renewable-gen")
    )
    assert len(generators) == 5

    with pytest.raises(ISOperationNotAllowed):
        system.get_component(RenewableGenerator, "renewable-gen")

    assert len(list(system.list_components_by_name(RenewableGenerator, "renewable-gen"))) == 5

    generator = all_components[0]
    assert system.get_component_by_uuid(generator.uuid) is generator
    with pytest.raises(ISNotStored):
        system.get_component_by_uuid(uuid4())

    stored_types = sorted((x.__name__ for x in system.get_component_types()))
    assert stored_types == [
        "Location",
        "RenewableGenerator",
        "SimpleBus",
        "SimpleGenerator",
        "SimpleSubsystem",
    ]


def test_get_component_by_label():
    system = SimpleSystem(auto_add_composed_components=True)
    gen = RenewableGenerator.example()
    system.add_component(gen)
    assert system.get_component_by_label(gen.label) is gen
    with pytest.raises(ISNotStored):
        system.get_component_by_label("SimpleGenerator.invalid")
    with pytest.raises(ISNotStored):
        system.get_component_by_label("invalid.invalid")
    coordinates = gen.bus.coordinates
    assert coordinates is not None
    assert system.get_component_by_label(coordinates.label) is coordinates

    gen = RenewableGenerator.example()
    system.add_component(gen)
    with pytest.raises(ISOperationNotAllowed):
        system.get_component_by_label(gen.label)


def test_get_components_multiple_types():
    system = SimpleSystem()
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen3 = RenewableGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2, gen3)

    selected_components = list(system.get_components(SimpleBus, SimpleGenerator))
    assert len(selected_components) == 3  # 2 SimpleGenerator + 1 SimpleBus

    # Validate that filter_function works as well
    selected_components = list(
        system.get_components(
            SimpleGenerator, RenewableGenerator, filter_func=lambda x: x.name == "gen2"
        )
    )
    assert len(selected_components) == 2  # 1 SimpleGenerator + 1 RenewableGenerator


def test_component_associations(tmp_path):
    system = SimpleSystem()
    for i in range(3):
        geo = Location(x=i, y=i + 1)
        bus = SimpleBus(name=f"bus{i}", voltage=1.1, coordinates=geo)
        gen1 = SimpleGenerator(
            name=f"gen{i}a", active_power=1.0, rating=1.0, bus=bus, available=True
        )
        gen2 = SimpleGenerator(
            name=f"gen{i}b", active_power=1.0, rating=1.0, bus=bus, available=True
        )
        subsystem = SimpleSubsystem(name=f"test-subsystem{i}", generators=[gen1, gen2])
        system.add_components(geo, bus, gen1, gen2, subsystem)

    def check_attached_components(my_sys, parent_type, child_type):
        for i in range(3):
            bus = my_sys.get_component(SimpleBus, f"bus{i}")
            gen1 = my_sys.get_component(SimpleGenerator, f"gen{i}a")
            gen2 = my_sys.get_component(SimpleGenerator, f"gen{i}b")
            attached = my_sys.list_parent_components(bus, component_type=parent_type)
            assert len(attached) == 2
            labels = {gen1.label, gen2.label}
            for component in attached:
                assert component.label in labels
                attached_subsystems = my_sys.list_parent_components(component)
                assert len(attached_subsystems) == 1
                assert attached_subsystems[0].name == f"test-subsystem{i}"
                assert not my_sys.list_parent_components(attached_subsystems[0])
                assert my_sys.list_child_components(component) == [bus]
                assert my_sys.list_child_components(component, component_type=child_type) == [bus]

            for component in (bus, gen1, gen2):
                with pytest.raises(ISOperationNotAllowed):
                    my_sys.remove_component(component)

    check_attached_components(system, SimpleGenerator, SimpleBus)
    check_attached_components(system, GeneratorBase, Component)
    check_attached_components(system, Component, Component)
    system._component_mgr._associations.clear()
    for component in system.iter_all_components():
        assert not system.list_parent_components(component)

    system.rebuild_component_associations()
    check_attached_components(system, SimpleGenerator, SimpleBus)
    check_attached_components(system, GeneratorBase, Component)

    save_dir = tmp_path / "test_system"
    system.save(save_dir)
    system2 = SimpleSystem.from_json(save_dir / "system.json")
    check_attached_components(system2, SimpleGenerator, SimpleBus)
    check_attached_components(system2, GeneratorBase, Component)

    bus = system2.get_component(SimpleBus, "bus1")
    with pytest.raises(ISOperationNotAllowed):
        system2.remove_component(bus)
    system2.remove_component(bus, force=True)
    gen = system2.get_component(SimpleGenerator, "gen1a")
    with pytest.raises(ISNotStored):
        system2.get_component(SimpleBus, gen.bus.name)


def test_single_time_series_attach_from_array():
    system = SimpleSystem()
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)
    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts, gen1, gen2)
    assert system.has_time_series(gen1, name=variable_name)
    assert system.has_time_series(gen2, name=variable_name)
    assert np.array_equal(
        system.get_time_series(gen1, time_series_type=SingleTimeSeries, name=variable_name).data,
        ts.data,
    )


def test_single_time_series():
    system = SimpleSystem()
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)

    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    time_array = [initial_time + timedelta(hours=i) for i in range(length)]
    data = range(length)
    variable_name = "active_power"
    ts = SingleTimeSeries.from_time_array(data, variable_name, time_array)
    with pytest.raises(ValueError, match="The first argument must"):
        # Test a common mistake.
        system.add_time_series(gen1, ts)  # type: ignore

    system.add_time_series(ts, gen1, gen2)
    assert system.has_time_series(gen1, name=variable_name)
    assert system.has_time_series(gen2, name=variable_name)
    assert system.get_time_series(gen1, name=variable_name) == ts
    system.remove_time_series(gen1, gen2, name=variable_name)
    with pytest.raises(ISNotStored):
        system.get_time_series(gen1, name=variable_name)

    assert not system.has_time_series(gen1, name=variable_name)
    assert not system.has_time_series(gen2, name=variable_name)


TS_STORAGE_OPTIONS = (
    TimeSeriesStorageType.ARROW,
    TimeSeriesStorageType.CHRONIFY,
    TimeSeriesStorageType.MEMORY,
)


@pytest.mark.parametrize(
    "storage_type,use_quantity",
    list(itertools.product(TS_STORAGE_OPTIONS, [True, False])),
)
def test_time_series_retrieval(storage_type, use_quantity):
    system = SimpleSystem(time_series_storage_type=storage_type)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)

    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    time_array = [initial_time + timedelta(hours=i) for i in range(length)]
    data = (
        [ActivePower(np.random.rand(length), "watts") for _ in range(4)]
        if use_quantity
        else [np.random.rand(length) for _ in range(4)]  # type: ignore
    )
    variable_name = "active_power"
    ts1 = SingleTimeSeries.from_time_array(data[0], variable_name, time_array)
    ts2 = SingleTimeSeries.from_time_array(data[1], variable_name, time_array)
    ts3 = SingleTimeSeries.from_time_array(data[2], variable_name, time_array)
    ts4 = SingleTimeSeries.from_time_array(data[3], variable_name, time_array)
    system.add_time_series(ts1, gen, scenario="high", model_year="2030")
    system.add_time_series(ts2, gen, scenario="high", model_year="2035")
    system.add_time_series(ts3, gen, scenario="low", model_year="2030")
    key4 = system.add_time_series(ts4, gen, scenario="low", model_year="2035")
    assert len(system.list_time_series_keys(gen)) == 4
    assert len(system.list_time_series_metadata(gen)) == 4
    assert len(system.list_time_series_metadata(gen, scenario="high", model_year="2035")) == 1
    assert (
        system.list_time_series_metadata(gen, scenario="high", model_year="2035")[0].features[
            "model_year"
        ]
        == "2035"
    )
    assert len(system.list_time_series_metadata(gen, scenario="low")) == 2
    for metadata in system.list_time_series_metadata(gen, scenario="high"):
        assert metadata.features["scenario"] == "high"

    assert all(
        np.equal(
            system.get_time_series(gen, variable_name, scenario="high", model_year="2030").data,
            ts1.data,
        )
    )
    assert all(
        np.equal(
            system.get_time_series(gen, variable_name, scenario="high", model_year="2035").data,
            ts2.data,
        )
    )
    assert all(
        np.equal(
            system.get_time_series(gen, variable_name, scenario="low", model_year="2030").data,
            ts3.data,
        )
    )
    assert all(
        np.equal(
            system.get_time_series_by_key(gen, key4).data,
            ts4.data,
        )
    )

    with pytest.raises(ISAlreadyAttached):
        system.add_time_series(ts4, gen, scenario="low", model_year="2035")

    assert system.has_time_series(gen, name=variable_name)
    assert system.has_time_series(gen, name=variable_name, scenario="high")
    assert system.has_time_series(gen, name=variable_name, scenario="high", model_year="2030")
    assert not system.has_time_series(gen, name=variable_name, model_year="2036")
    with pytest.raises(ISOperationNotAllowed):
        system.get_time_series(gen, name=variable_name, scenario="high")
    with pytest.raises(ISNotStored):
        system.get_time_series(gen, name=variable_name, scenario="medium")
    assert len(system.list_time_series(gen, name=variable_name, scenario="high")) == 2
    assert len(system.list_time_series(gen, name=variable_name)) == 4
    system.remove_time_series(gen, name=variable_name, scenario="high")
    assert len(system.list_time_series(gen, name=variable_name)) == 2
    system.remove_time_series(gen, name=variable_name)
    assert not system.has_time_series(gen, name=variable_name)


@pytest.mark.parametrize("storage_type", TS_STORAGE_OPTIONS)
def test_open_time_series_store(storage_type: TimeSeriesStorageType):
    system = SimpleSystem(time_series_storage_type=storage_type)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)

    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    timestamps = [initial_time + timedelta(hours=i) for i in range(length)]
    time_series_arrays: list[SingleTimeSeries] = []
    with system.open_time_series_store() as conn:
        for i in range(5):
            ts = SingleTimeSeries.from_time_array(np.random.rand(length), f"ts{i}", timestamps)
            system.add_time_series(ts, gen)
            time_series_arrays.append(ts)
    with system.open_time_series_store() as conn:
        for i in range(5):
            ts = system.get_time_series(gen, name=f"ts{i}", context=conn)
            assert np.array_equal(
                system.get_time_series(gen, f"ts{i}").data, time_series_arrays[i].data
            )


def test_time_series_removal():
    system = SimpleSystem()
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)

    variable_names = ["active_power", "reactive_power"]
    uuids = []
    for variable_name in variable_names:
        length = 8784
        data = range(length)
        start = datetime(year=2020, month=1, day=1)
        resolution = timedelta(hours=1)
        ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
        uuids.append(ts.uuid)
        for gen in (gen1, gen2):
            system.add_time_series(ts, gen, scenario="high", model_year="2030")
            system.add_time_series(ts, gen, scenario="high", model_year="2035")
            system.add_time_series(ts, gen, scenario="low", model_year="2030")
            system.add_time_series(ts, gen, scenario="low", model_year="2035")

    system.remove_time_series(gen1, name="active_power")
    system.remove_time_series(gen1, name="reactive_power")
    assert not system.list_time_series(gen1, name="active_power")
    assert not system.list_time_series(gen1, name="reactive_power")
    assert system.list_time_series(gen2, name="active_power")
    assert system.list_time_series(gen2, name="reactive_power")
    system.remove_time_series(gen2)
    assert not system.list_time_series(gen2, name="active_power")
    assert not system.list_time_series(gen2, name="reactive_power")


def test_time_series_read_only():
    system = SimpleSystem(time_series_read_only=True)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_component(bus)
    system.add_component(gen)

    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    with pytest.raises(ISOperationNotAllowed):
        system.add_time_series(ts, gen)


def test_serialize_time_series_from_array(tmp_path):
    system = SimpleSystem()
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)

    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts, gen1, gen2, scenario="high", model_year="2030")
    filename = tmp_path / "system.json"
    system.to_json(filename)

    system2 = SimpleSystem.from_json(filename, time_series_read_only=True)
    gen1b = system2.get_component(SimpleGenerator, gen1.name)
    with pytest.raises(ISOperationNotAllowed):
        system2.remove_time_series(gen1b, name=variable_name)
    ts2 = system.get_time_series(gen1b, name=variable_name)
    assert ts2.data.tolist() == list(data)


@pytest.mark.parametrize("storage_type", TS_STORAGE_OPTIONS)
def test_time_series_slices(storage_type):
    system = SimpleSystem(
        name="test-system",
        auto_add_composed_components=True,
        time_series_storage_type=storage_type,
    )
    gen = SimpleGenerator.example()
    system.add_components(gen)
    variable_name = "active_power"
    length = 8784
    data = list(range(length))
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts, gen)

    first_timestamp = start
    second_timestamp = start + resolution
    last_timestamp = start + (length - 1) * resolution
    ts_tmp = system.time_series.get(gen, name=variable_name)
    assert isinstance(ts_tmp, SingleTimeSeries)
    assert len(ts_tmp.data) == length
    ts_tmp = system.time_series.get(gen, name=variable_name, length=10)
    assert isinstance(ts_tmp, SingleTimeSeries)
    assert len(ts_tmp.data) == 10
    ts2 = system.time_series.get(gen, name=variable_name, start_time=second_timestamp, length=5)
    assert isinstance(ts2, SingleTimeSeries)
    assert len(ts2.data) == 5
    assert ts2.data.tolist() == data[1:6]

    ts_tmp = system.time_series.get(gen, name=variable_name, start_time=second_timestamp)
    assert isinstance(ts_tmp, SingleTimeSeries)
    assert len(ts_tmp.data) == len(data) - 1

    with pytest.raises(ISConflictingArguments, match="is less than"):
        system.time_series.get(
            gen,
            name=variable_name,
            start_time=first_timestamp - ts.resolution,
            length=5,
        )
    with pytest.raises(ISConflictingArguments, match="is too large"):
        system.time_series.get(
            gen,
            name=variable_name,
            start_time=last_timestamp + ts.resolution,
            length=5,
        )
    with pytest.raises(ISConflictingArguments, match="conflicts with initial_time"):
        system.time_series.get(
            gen,
            name=variable_name,
            start_time=first_timestamp + timedelta(minutes=1),
        )
    with pytest.raises(ISConflictingArguments, match=r"start_time.*length.*conflicts with"):
        system.time_series.get(
            gen,
            name=variable_name,
            start_time=second_timestamp,
            length=len(data),
        )


def test_copy_component(simple_system_with_time_series: SimpleSystem):
    system = simple_system_with_time_series
    gen1 = system.get_component(SimpleGenerator, "test-gen")

    gen2 = system.copy_component(gen1)
    assert gen2.uuid != gen1.uuid
    assert gen2.name == gen1.name
    assert gen2.bus is gen1.bus

    gen3 = system.copy_component(gen1, name="gen3")
    assert gen3.name == "gen3"

    gen4 = system.copy_component(gen1, name="gen4", attach=True)
    assert gen4.name == "gen4"


def test_deepcopy_component(simple_system_with_time_series: SimpleSystem):
    system = simple_system_with_time_series
    gen1 = system.get_component(SimpleGenerator, "test-gen")
    subsystem = SimpleSubsystem(name="subsystem1", generators=[gen1])
    system.add_component(subsystem)
    gen2 = system.deepcopy_component(gen1)
    assert gen2.name == gen1.name
    assert gen2.uuid == gen1.uuid
    assert gen2.bus.uuid == gen1.bus.uuid
    assert gen2.bus is not gen1.bus


@pytest.mark.parametrize("inputs", list(itertools.product(TS_STORAGE_OPTIONS, [True, False])))
def test_remove_component(inputs):
    storage_type, cascade_down = inputs
    system = SimpleSystem(
        name="test-system",
        auto_add_composed_components=True,
        time_series_storage_type=storage_type,
    )
    gen1 = SimpleGenerator.example()
    bus = gen1.bus
    system.add_components(gen1)
    gen2 = system.copy_component(gen1, name="gen2", attach=True)
    assert gen2.bus is bus
    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts, gen1, gen2)

    system.remove_component_by_name(type(gen1), gen1.name, cascade_down=cascade_down)
    assert system.has_component(bus)
    assert not system.has_time_series(gen1)
    assert system.has_time_series(gen2)

    system.remove_component_by_uuid(gen2.uuid, cascade_down=cascade_down)
    assert system.has_component(bus) != cascade_down
    assert not system.has_time_series(gen2)

    with pytest.raises(ISNotStored):
        system.remove_component(gen2)

    with pytest.raises(ISNotStored):
        system.remove_component(gen2)

    for gen in (gen1, gen2):
        with pytest.raises(ISNotStored):
            system.get_component(SimpleGenerator, gen.name)


def test_system_to_dict():
    system = SimpleSystem(
        name="test-system",
        auto_add_composed_components=True,
    )
    gen1 = SimpleGenerator.example()
    gen2 = SimpleGenerator.example()
    gen3 = SimpleGenerator.example()
    system.add_components(gen1, gen2, gen3)

    component_dict: list[dict] = list(system.to_records(SimpleGenerator))
    assert len(component_dict) == 3  # 3 generators
    assert component_dict[0].get("uuid") is not None
    assert component_dict[0]["bus"] == gen1.bus.label

    exclude_first_level_fields = {"name": True, "available": True}
    component_dict = list(system.to_records(SimpleGenerator, exclude=exclude_first_level_fields))
    assert len(component_dict) == 3
    assert component_dict[0].get("name", None) is None
    assert component_dict[0].get("available", None) is None

    component_dict = list(system.to_records(SimpleGenerator))
    assert len(component_dict) == 3  # 3 generators
    assert component_dict[0]["bus"] == gen1.bus.label

    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts, gen1)
    component_dicts = list(system.to_records(SimpleGenerator))
    assert len(component_dicts) == 3  # 3 generators


def test_time_series_metadata_sql():
    system = SimpleSystem(name="test-system", auto_add_composed_components=True)
    gen1 = SimpleGenerator.example()
    system.add_components(gen1)
    gen2 = system.copy_component(gen1, name="gen2", attach=True)
    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts1 = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    ts2 = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts1, gen1)
    system.add_time_series(ts2, gen2)
    rows = system.time_series.metadata_store.sql(
        f"""
        SELECT owner_type, time_series_type, owner_uuid, time_series_uuid
        FROM {TIME_SERIES_ASSOCIATIONS_TABLE}
        WHERE owner_uuid = '{gen1.uuid}'
    """
    )
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == SimpleGenerator.__name__
    assert row[1] == SingleTimeSeries.__name__
    assert row[2] == str(gen1.uuid)
    assert row[3] == str(ts1.uuid)


def test_time_series_metadata_list_rows():
    system = SimpleSystem(name="test-system", auto_add_composed_components=True)
    gen1 = SimpleGenerator.example()
    system.add_components(gen1)
    gen2 = system.copy_component(gen1, name="gen2", attach=True)
    variable_name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts1 = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    ts2 = SingleTimeSeries.from_array(data, variable_name, start, resolution)
    system.add_time_series(ts1, gen1)
    system.add_time_series(ts2, gen2)
    columns = [
        "owner_type",
        "time_series_type",
        "owner_uuid",
        "time_series_uuid",
    ]
    rows = system.time_series.metadata_store.list_rows(
        gen2,
        name=variable_name,
        time_series_type=SingleTimeSeries.__name__,
        columns=columns,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == SimpleGenerator.__name__
    assert row[1] == SingleTimeSeries.__name__
    assert row[2] == str(gen2.uuid)
    assert row[3] == str(ts2.uuid)


def test_system_counts():
    system = SimpleSystem(name="test-system", auto_add_composed_components=True)
    gen1 = SimpleGenerator.example()
    gen2 = SimpleGenerator.example()
    system.add_components(gen1, gen2)
    variable_name = "active_power"
    data = range(10)

    def add_time_series(iteration, initial_time, resolution):
        for i in range(5):
            ts1 = SingleTimeSeries.from_array(
                data,
                f"{variable_name}_{iteration}_{i}",
                initial_time + resolution * i,
                resolution,
            )
            ts2 = SingleTimeSeries.from_array(
                data,
                f"{variable_name}_{iteration}_{i}",
                initial_time + resolution * i,
                resolution,
            )
            system.add_time_series(ts1, gen1, gen2)
            system.add_time_series(ts2, gen1.bus)

    add_time_series(1, datetime(year=2020, month=1, day=1), timedelta(hours=1))
    add_time_series(2, datetime(year=2020, month=2, day=1), timedelta(minutes=5))

    # 2 generators, 2 buses, 2 locations
    assert system._components.get_num_components() == 6
    components_by_type = system._components.get_num_components_by_type()
    assert components_by_type[SimpleGenerator] == 2
    assert components_by_type[SimpleBus] == 2
    ts_counts = system.time_series.metadata_store.get_time_series_counts()
    assert ts_counts.time_series_count == 2 * 10
    assert (
        ts_counts.time_series_type_count[
            (
                "SimpleGenerator",
                "SingleTimeSeries",
                "2020-01-01T02:00:00",
                to_iso_8601(timedelta(hours=1)),
            )
        ]
        == 2
    )
    assert (
        ts_counts.time_series_type_count[
            (
                "SimpleBus",
                "SingleTimeSeries",
                "2020-02-01T00:10:00",
                to_iso_8601(timedelta(minutes=5)),
            )
        ]
        == 1
    )


def test_system_printing(simple_system_with_time_series):
    simple_system_with_time_series.info()


def test_system_show_components(simple_system_with_time_series):
    simple_system_with_time_series.show_components(SimpleBus)
    simple_system_with_time_series.show_components(SimpleBus, show_uuid=True)
    simple_system_with_time_series.show_components(SimpleBus, show_time_series=True)
    simple_system_with_time_series.show_components(SimpleBus, show_supplemental=True)


def test_system_info_renders_supplemental_attributes_table(
    simple_system_with_supplemental_attributes,
    capsys,
):
    """Test supplemental attributes appear in System table."""
    simple_system_with_supplemental_attributes.info()

    output = capsys.readouterr().out

    assert "Supplemental Attributes attached" in output
    assert "3" in output  # Total count


def test_system_info_no_table_when_empty(simple_system_with_time_series, capsys):
    """Test supplemental attributes shows 0 when none exist."""
    simple_system_with_time_series.info()

    output = capsys.readouterr().out
    assert "Supplemental Attributes attached" in output


def test_system_info_table_content_accuracy(
    simple_system_with_supplemental_attributes,
    capsys,
):
    """Test supplemental attributes shows correct count."""
    system = simple_system_with_supplemental_attributes

    assert system.get_num_supplemental_attributes() == 3

    system.info()
    output = capsys.readouterr().out

    assert "Supplemental Attributes attached" in output
    assert "3" in output


def test_system_info_with_nonsequential_time_series(
    simple_system_with_nonsequential_time_series, capsys
):
    """Test that info() handles NonSequentialTimeSeries without crashing."""
    simple_system_with_nonsequential_time_series.info()

    output = capsys.readouterr().out
    assert "Time Series Summary" in output
    assert "NonSequen" in output
    assert "N/A" in output


def test_system_info_with_mixed_time_series(simple_system_with_mixed_time_series, capsys):
    """Test that info() handles a mix of SingleTimeSeries and NonSequentialTimeSeries."""
    simple_system_with_mixed_time_series.info()

    output = capsys.readouterr().out
    assert "Time Series Summary" in output
    assert "SingleTim" in output
    assert "NonSequen" in output
    assert "N/A" in output


def test_single_attribute_type(simple_system):
    """Test with only one attribute type."""
    from infrasys.location import GeographicInfo

    bus = simple_system.get_component(SimpleBus, "test-bus")
    attr = GeographicInfo.example()
    simple_system.add_supplemental_attribute(bus, attr)

    simple_system.info()  # Should not crash

    counts = simple_system.get_supplemental_attribute_counts_by_type()
    assert len(counts) == 1
    assert counts[0]["type"] == "GeographicInfo"


def test_many_supplemental_attributes(simple_system):
    """Test with 50 attributes."""
    from infrasys.location import GeographicInfo

    components = []
    for i in range(10):
        bus = SimpleBus(name=f"bus-{i}", voltage=1.1)
        simple_system.add_component(bus)
        components.append(bus)

    for i in range(50):
        attr = GeographicInfo.example()
        attr.geo_json["properties"]["name"] = f"Location-{i}"
        simple_system.add_supplemental_attribute(components[i % 10], attr)

    simple_system.info()  # Should not crash
    assert simple_system.get_num_supplemental_attributes() == 50


def test_convert_chronify_to_arrow_in_deserialize(tmp_path):
    system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)
    assert isinstance(system.time_series.storage, ChronifyTimeSeriesStorage)
    assert system.time_series.storage.get_database_url()
    assert system.time_series.storage.get_engine_name() == "duckdb"
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)
    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    timestamps = [initial_time + timedelta(hours=i) for i in range(length)]
    ts = SingleTimeSeries.from_time_array(np.random.rand(length), "test_ts", timestamps)
    system.add_time_series(ts, gen)
    filename = tmp_path / "system.json"
    system.to_json(filename)
    system2 = SimpleSystem.from_json(
        filename, time_series_storage_type=TimeSeriesStorageType.ARROW
    )
    assert isinstance(system2.time_series.storage, ArrowTimeSeriesStorage)
    gen2 = system2.get_component(SimpleGenerator, "gen")
    ts2 = system2.get_time_series(gen2, "test_ts")
    assert np.array_equal(ts.data, ts2.data)


def test_chronfiy_storage():
    system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)
    assert isinstance(system.time_series.storage, ChronifyTimeSeriesStorage)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)
    time_series: list[SingleTimeSeries] = []
    for i in range(2):
        for initial_time, resolution, length in (
            (datetime(year=2020, month=1, day=1), timedelta(hours=1), 10),
            (datetime(year=2020, month=2, day=1), timedelta(minutes=5), 15),
        ):
            data = np.random.rand(length)
            name = f"test_ts_{length}_{i}"
            ts = SingleTimeSeries.from_array(data, name, initial_time, resolution)
            system.add_time_series(ts, gen)
            time_series.append(ts)

    for expected_ts in time_series:
        actual_ts = system.get_time_series(
            gen, time_series_type=SingleTimeSeries, name=expected_ts.name
        )
        assert np.array_equal(expected_ts.data, actual_ts.data)


def test_bulk_add_time_series():
    system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)
    assert isinstance(system.time_series.storage, ChronifyTimeSeriesStorage)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)
    time_series: list[SingleTimeSeries] = []
    keys: list[TimeSeriesKey] = []
    with system.open_time_series_store() as conn:
        for i in range(2):
            for initial_time, resolution, length in (
                (datetime(year=2020, month=1, day=1), timedelta(hours=1), 10),
                (datetime(year=2020, month=2, day=1), timedelta(minutes=5), 15),
            ):
                data = np.random.rand(length)
                name = f"test_ts_{length}_{i}"
                ts = SingleTimeSeries.from_array(data, name, initial_time, resolution)
                key = system.add_time_series(ts, gen, context=conn)
                keys.append(key)
                time_series.append(ts)

        for key in keys:
            system.time_series.storage.check_timestamps(key, context=conn.data_context)

    with system.open_time_series_store() as conn:
        for expected_ts in time_series:
            actual_ts = system.get_time_series(
                gen,
                time_series_type=SingleTimeSeries,
                name=expected_ts.name,
                context=conn,
            )
            assert np.array_equal(expected_ts.data, actual_ts.data)


@pytest.mark.parametrize("storage_type", TS_STORAGE_OPTIONS)
def test_bulk_add_time_series_with_rollback(storage_type: TimeSeriesStorageType):
    system = SimpleSystem(time_series_storage_type=storage_type)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen)
    ts_name = "test_ts"
    with pytest.raises(ISAlreadyAttached):
        with system.open_time_series_store() as conn:
            initial_time = datetime(year=2020, month=1, day=1)
            resolution = timedelta(hours=1)
            length = 10
            data = np.random.rand(length)
            ts = SingleTimeSeries.from_array(data, ts_name, initial_time, resolution)
            system.add_time_series(ts, gen, context=conn)
            assert system.has_time_series(gen, name=ts_name)
            system.add_time_series(ts, gen, context=conn)

    assert not system.has_time_series(gen, name=ts_name)
