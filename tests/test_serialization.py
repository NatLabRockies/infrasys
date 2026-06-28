import os
import random
import zipfile
from datetime import datetime, timedelta
from typing import Type

import numpy as np
import orjson
import pint
import pytest
from numpy._typing import NDArray
from pydantic import WithJsonSchema
from typing_extensions import Annotated

from infrasys import Location, SingleTimeSeries, NonSequentialTimeSeries, System
from infrasys.component import Component
from infrasys.exceptions import ISInvalidParameter, ISOperationNotAllowed
from infrasys.quantities import ActivePower, Distance
from infrasys.time_series_models import (
    TimeSeriesData,
    TimeSeriesStorageType,
)

from .models.simple_system import (
    SimpleBus,
    SimpleGenerator,
    SimpleSubsystem,
    SimpleSystem,
)

TS_STORAGE_OPTIONS = (
    TimeSeriesStorageType.TIME_SERIES_STORE,
    TimeSeriesStorageType.MEMORY,
)

TS_STORAGE_OPTIONS_NONSEQUENTIAL = TS_STORAGE_OPTIONS


class ComponentWithPintQuantity(Component):
    """Test component with a container of quantities."""

    distance: Annotated[Distance, WithJsonSchema({"type": "string"})]


def test_serialization(tmp_path):
    system = SimpleSystem(name="test-system", description="a test system", my_attr=5)
    num_components_by_type = 5
    for i in range(num_components_by_type):
        geo = Location(x=random.random(), y=random.random())
        bus = SimpleBus(name=f"test-bus{i}", voltage=random.random(), coordinates=geo)
        gen1 = SimpleGenerator(
            name=f"test-gen{i}a",
            active_power=random.random(),
            rating=random.random(),
            bus=bus,
            available=True,
        )
        gen2 = SimpleGenerator(
            name=f"test-gen{i}b",
            active_power=random.random(),
            rating=random.random(),
            bus=bus,
            available=True,
        )
        subsystem = SimpleSubsystem(name="test-subsystem", generators=[gen1, gen2])
        system.add_components(geo, bus, gen1, gen2, subsystem)

    components = list(system.iter_all_components())
    num_components = len(components)
    assert num_components == num_components_by_type * (1 + 1 + 2 + 1)

    filename = tmp_path / "system.json"
    system.to_json(filename, overwrite=True)
    system2 = SimpleSystem.from_json(filename)
    for key, val in system.__dict__.items():
        if key not in (
            "_component_mgr",
            "_supplemental_attr_mgr",
            "_time_series_mgr",
            "_con",
        ):
            assert getattr(system2, key) == val

    components2 = list(system2.iter_all_components())
    assert len(components2) == num_components

    for component in components:
        component2 = system2.get_component_by_id(component.id)
        for key, val in component.__dict__.items():
            if key == "legacy_uuid":
                continue
            if isinstance(val, Component):
                assert getattr(component2, key).id == val.id
            elif isinstance(val, list) and val and isinstance(val[0], Component):
                assert [x.id for x in getattr(component2, key)] == [x.id for x in val]
            else:
                assert getattr(component2, key) == val


def test_component_serialization_uses_integer_ids(tmp_path):
    system = SimpleSystem(name="test-system", auto_add_composed_components=True)
    gen = SimpleGenerator.example()
    system.add_component(gen)

    filename = tmp_path / "system.json"
    system.to_json(filename, overwrite=True)
    data = orjson.loads(filename.read_bytes())

    components = data["components"]
    assert all(isinstance(component["id"], int) for component in components)
    assert all("uuid" not in component for component in components)
    assert all("legacy_uuid" not in component for component in components)

    serialized_gen = next(
        component
        for component in components
        if component["__metadata__"]["type"] == SimpleGenerator.__name__
    )
    bus_reference = serialized_gen["bus"]["__metadata__"]
    assert isinstance(bus_reference["id"], int)
    assert "uuid" not in bus_reference

    system2 = SimpleSystem.from_json(filename)
    gen2 = system2.get_component_by_id(gen.id)
    assert gen2.name == gen.name
    assert gen2.bus.id == gen.bus.id


@pytest.mark.parametrize("time_series_storage_type", TS_STORAGE_OPTIONS)
def test_serialize_single_time_series(tmp_path, time_series_storage_type):
    system = SimpleSystem(time_series_storage_type=time_series_storage_type)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)

    name = "active_power"
    length = 8784
    data = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, name, start, resolution)
    system.add_time_series(ts, gen1, gen2, scenario="high", model_year="2030")
    filename = tmp_path / "system.json"
    system.to_json(filename)
    system2 = check_deserialize_with_read_write_time_series(filename)
    gen1b = system2.get_component(SimpleGenerator, gen1.name)
    gen2b = system2.get_component(SimpleGenerator, gen2.name)
    data2 = range(1, length + 1)
    ts2 = SingleTimeSeries.from_array(data2, name, start, resolution)
    system2.add_time_series(ts2, gen1b, gen2b, scenario="low", model_year="2030")
    filename2 = tmp_path / "system2.json"
    system2.to_json(filename2)
    system3 = SimpleSystem.from_json(filename2)
    assert np.array_equal(
        system3.get_time_series(
            gen1b,
            time_series_type=SingleTimeSeries,
            name=name,
            scenario="low",
            model_year="2030",
        ).data,
        data2,
    )
    assert np.array_equal(
        system3.get_time_series(
            gen2b,
            time_series_type=SingleTimeSeries,
            name=name,
            scenario="low",
            model_year="2030",
        ).data,
        data2,
    )
    check_deserialize_with_read_only_time_series(filename, gen1.name, gen2.name, name, ts.data)


def check_deserialize_with_read_only_time_series(
    filename,
    gen1_name: str,
    gen2_name: str,
    name: str,
    expected_ts_data: NDArray | pint.Quantity,
    expected_ts_timestamps: NDArray | None = None,
    time_series_type: Type[TimeSeriesData] = SingleTimeSeries,
):
    system = SimpleSystem.from_json(filename, time_series_read_only=True)
    system_ts_dir = system.get_time_series_directory()
    assert system_ts_dir is not None
    assert system_ts_dir == SimpleSystem._make_time_series_directory(filename)
    gen1b = system.get_component(SimpleGenerator, gen1_name)
    with pytest.raises(ISOperationNotAllowed):
        system.remove_time_series(gen1b, name=name)

    ts2 = system.get_time_series(gen1b, time_series_type=time_series_type, name=name)
    assert np.array_equal(ts2.data, expected_ts_data)
    if expected_ts_timestamps is not None:
        assert np.array_equal(ts2.timestamps, expected_ts_timestamps)


@pytest.mark.parametrize("time_series_storage_type", TS_STORAGE_OPTIONS_NONSEQUENTIAL)
def test_serialize_nonsequential_time_series(tmp_path, time_series_storage_type):
    "Test serialization of NonSequentialTimeSeries"
    system = SimpleSystem(time_series_storage_type=time_series_storage_type)
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen1 = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    system.add_components(bus, gen1, gen2)

    name = "active_power"
    length = 10
    data = range(length)
    timestamps = [
        datetime(year=2030, month=1, day=1) + timedelta(seconds=5 * i) for i in range(length)
    ]
    ts = NonSequentialTimeSeries.from_array(data=data, name=name, timestamps=timestamps)
    system.add_time_series(ts, gen1, gen2, scenario="high", model_year="2030")
    filename = tmp_path / "system.json"
    system.to_json(filename)

    check_deserialize_with_read_write_time_series(filename)
    check_deserialize_with_read_only_time_series(
        filename,
        gen1.name,
        gen2.name,
        name,
        ts.data,
        ts.timestamps,
        time_series_type=NonSequentialTimeSeries,
    )


def check_deserialize_with_read_write_time_series(filename) -> System:
    system3 = SimpleSystem.from_json(filename, time_series_read_only=False)
    assert system3.get_time_series_directory() != SimpleSystem._make_time_series_directory(
        filename
    )
    system3_ts_dir = system3.get_time_series_directory()
    assert system3_ts_dir is not None
    return system3


@pytest.mark.parametrize(
    "distance",
    [
        Distance(2, "meter"),
        Distance([2, 3], "meter"),
        Distance([[2, 3, 4], [5, 6, 7]], "meter"),
    ],
)
def test_serialize_quantity(tmp_path, distance):
    system = SimpleSystem()
    gen = SimpleGenerator.example()
    component = ComponentWithPintQuantity(name="test", distance=distance)
    assert gen.bus.coordinates is not None
    system.add_components(gen.bus.coordinates, gen.bus, gen, component)
    sys_file = tmp_path / "system.json"
    system.to_json(sys_file)
    system2 = SimpleSystem.from_json(sys_file)
    c1 = system.get_component(ComponentWithPintQuantity, "test")
    c2 = system2.get_component(ComponentWithPintQuantity, "test")
    if isinstance(c1.distance.magnitude, np.ndarray):
        assert (c2.distance == c1.distance).all()  # type: ignore
    else:
        assert c2.distance == c1.distance


def test_with_single_time_series_quantity(tmp_path):
    """Test serialization of SingleTimeSeries with a Pint quantity."""
    system = SimpleSystem(auto_add_composed_components=True)
    gen = SimpleGenerator.example()
    system.add_components(gen)
    length = 10
    initial_time = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    data = ActivePower(range(length), "watts")
    name = "active_power"
    ts = SingleTimeSeries.from_array(data, name, initial_time, resolution)
    system.add_time_series(ts, gen)

    sys_file = tmp_path / "system.json"
    system.to_json(sys_file)

    system2 = SimpleSystem.from_json(sys_file)
    gen2 = system2.get_component(SimpleGenerator, gen.name)
    ts2 = system2.get_time_series(gen2, time_series_type=SingleTimeSeries, name=name)
    assert isinstance(ts, SingleTimeSeries)
    assert ts.length == length
    assert ts.resolution == resolution
    assert ts.initial_timestamp == initial_time
    assert isinstance(ts2.data.magnitude, np.ndarray)
    assert np.array_equal(ts2.data.magnitude, np.array(range(length)))


def test_with_nonsequential_time_series_quantity(tmp_path):
    """Test serialization of SingleTimeSeries with a Pint quantity."""
    system = SimpleSystem(auto_add_composed_components=True)
    gen = SimpleGenerator.example()
    system.add_components(gen)
    length = 10
    data = ActivePower(range(length), "watts")
    name = "active_power"
    timestamps = [
        datetime(year=2030, month=1, day=1) + timedelta(seconds=100 * i) for i in range(10)
    ]
    ts = NonSequentialTimeSeries.from_array(data=data, name=name, timestamps=timestamps)
    system.add_time_series(ts, gen)

    sys_file = tmp_path / "system.json"
    system.to_json(sys_file)

    system2 = SimpleSystem.from_json(sys_file)
    gen2 = system2.get_component(SimpleGenerator, gen.name)
    ts2 = system2.get_time_series(gen2, time_series_type=NonSequentialTimeSeries, name=name)
    assert isinstance(ts, NonSequentialTimeSeries)
    assert ts.length == length
    assert isinstance(ts2.data.magnitude, np.ndarray)
    assert isinstance(ts2.timestamps, np.ndarray)
    assert np.array_equal(ts2.data.magnitude, np.array(range(length)))
    assert np.array_equal(ts2.timestamps, np.array(timestamps))


def test_json_schema():
    schema = ComponentWithPintQuantity.model_json_schema()
    assert isinstance(orjson.loads(orjson.dumps(schema)), dict)


def test_system_save(tmp_path, simple_system_with_time_series):
    simple_system = simple_system_with_time_series
    custom_folder = "my_system"
    fpath = tmp_path / custom_folder
    fname = "test_system.json"
    simple_system.save(fpath, filename=fname)
    assert os.path.exists(fpath), f"Folder {fpath} was not created successfully"
    assert os.path.exists(fpath / fname), f"Serialized system {fname} was not created successfully"

    with pytest.raises(FileExistsError):
        simple_system.save(fpath, filename=fname)

    simple_system.save(fpath, filename=fname, overwrite=True)
    assert os.path.exists(fpath), f"Folder {fpath} was not created successfully"
    assert os.path.exists(fpath / fname), f"Serialized system {fname} was not created successfully"

    custom_folder = "my_system_zip"
    fpath = tmp_path / custom_folder
    simple_system.save(fpath, filename=fname, zip=True)
    assert not os.path.exists(fpath), f"Original folder {fpath} was not deleted sucessfully."
    zip_fpath = f"{fpath}.zip"
    assert os.path.exists(zip_fpath), f"Zip file {zip_fpath} does not exists"


def test_system_load(tmp_path, simple_system_with_time_series):
    """Test loading a system from a zip archive."""
    simple_system = simple_system_with_time_series
    custom_folder = "load_test_system"
    fpath = tmp_path / custom_folder
    fname = "test_system.json"

    simple_system.save(fpath, filename=fname, zip=True)
    zip_fpath = f"{fpath}.zip"
    assert os.path.exists(zip_fpath), f"Zip file {zip_fpath} was not created"
    assert not os.path.exists(fpath), f"Original folder {fpath} was not deleted"

    loaded_system = SimpleSystem.load(zip_fpath)
    assert loaded_system is not None
    assert loaded_system.name == simple_system.name
    assert loaded_system.description == simple_system.description

    original_buses = list(simple_system.get_components(SimpleBus))
    loaded_buses = list(loaded_system.get_components(SimpleBus))
    assert len(loaded_buses) == len(original_buses)

    original_gens = list(simple_system.get_components(SimpleGenerator))
    loaded_gens = list(loaded_system.get_components(SimpleGenerator))
    assert len(loaded_gens) == len(original_gens)

    for orig_gen in original_gens:
        loaded_gen = loaded_system.get_component(SimpleGenerator, orig_gen.name)
        orig_ts_metadata = simple_system.list_time_series_metadata(orig_gen)
        loaded_ts_metadata = loaded_system.list_time_series_metadata(loaded_gen)
        assert len(loaded_ts_metadata) == len(orig_ts_metadata)


def test_system_load_errors(tmp_path):
    """Test error handling in System.load()."""
    with pytest.raises(FileNotFoundError, match="Zip file does not exist"):
        SimpleSystem.load(tmp_path / "nonexistent.zip")

    fake_zip = tmp_path / "fake.zip"
    fake_zip.write_text("This is not a zip file")
    with pytest.raises(ISInvalidParameter, match="not a valid zip archive"):
        SimpleSystem.load(fake_zip)

    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w") as zf:
        zf.writestr("readme.txt", "No JSON here")
    with pytest.raises(ISInvalidParameter, match="No JSON file found"):
        SimpleSystem.load(empty_zip)


@pytest.mark.parametrize("time_series_storage_type", TS_STORAGE_OPTIONS)
def test_system_save_load_with_storage_backends(tmp_path, time_series_storage_type):
    """Test save and load methods work correctly with different storage backends."""
    # Create a system with the specified storage backend
    system = SimpleSystem(
        name=f"test_system_{time_series_storage_type}",
        description=f"Test system with {time_series_storage_type} storage",
        auto_add_composed_components=True,
        time_series_storage_type=time_series_storage_type,
    )

    # Add components
    bus1 = SimpleBus(name="bus1", voltage=120.0)
    bus2 = SimpleBus(name="bus2", voltage=240.0)
    gen1 = SimpleGenerator(name="gen1", available=True, active_power=100.0, rating=150.0, bus=bus1)
    gen2 = SimpleGenerator(name="gen2", available=True, active_power=200.0, rating=250.0, bus=bus2)
    system.add_components(bus1, bus2, gen1, gen2)

    # Add time series data
    length = 24
    data = list(range(length))
    start = datetime(year=2024, month=1, day=1)
    resolution = timedelta(hours=1)

    ts1 = SingleTimeSeries.from_array(data, "max_active_power", start, resolution)
    ts2 = SingleTimeSeries.from_array([x * 2 for x in data], "max_active_power", start, resolution)

    system.add_time_series(ts1, gen1)
    system.add_time_series(ts2, gen2)

    save_dir = tmp_path / f"system_{time_series_storage_type}"
    system.save(save_dir, filename="system.json", zip=True)

    zip_path = f"{save_dir}.zip"
    assert os.path.exists(zip_path), f"Zip file not created for {time_series_storage_type}"
    assert not os.path.exists(save_dir), (
        f"Original directory not deleted for {time_series_storage_type}"
    )

    # Load from zip
    loaded_system = SimpleSystem.load(zip_path)

    # Verify system metadata
    assert loaded_system.name == system.name
    assert loaded_system.description == system.description

    # Verify components
    loaded_buses = list(loaded_system.get_components(SimpleBus))
    loaded_gens = list(loaded_system.get_components(SimpleGenerator))
    assert len(loaded_buses) == 2
    assert len(loaded_gens) == 2

    for orig_gen in [gen1, gen2]:
        loaded_gen = loaded_system.get_component(SimpleGenerator, orig_gen.name)

        # Check time series exists
        orig_ts_metadata = system.list_time_series_metadata(orig_gen)
        loaded_ts_metadata = loaded_system.list_time_series_metadata(loaded_gen)
        assert len(loaded_ts_metadata) == len(orig_ts_metadata) == 1

        orig_ts = system.get_time_series(orig_gen, "max_active_power")
        loaded_ts = loaded_system.get_time_series(loaded_gen, "max_active_power")

        assert len(loaded_ts.data) == len(orig_ts.data) == length
        assert list(loaded_ts.data) == list(orig_ts.data)
        assert loaded_ts.initial_timestamp == orig_ts.initial_timestamp
        assert loaded_ts.resolution == orig_ts.resolution


def test_convert_time_series_store_storage_permanent(tmp_path):
    gen = SimpleGenerator.example()
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_components(gen)
    name = "active_power"
    length = 10
    data = list(range(length))
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, name, start, resolution)
    system.add_time_series(ts, gen)
    storage = system.time_series.convert_storage(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
        in_place=False,
        permanent=True,
    )
    assert storage.get_time_series_directory() == tmp_path
    assert (tmp_path / "time_series_store.nc").exists()
    assert (tmp_path / "time_series_store.nc.sqlite").exists()


def test_serialized_component_reference_uuid_property_raises():
    """Test that SerializedComponentReference.uuid raises when legacy_uuid is None."""
    from infrasys.serialization import SerializedComponentReference

    ref = SerializedComponentReference(id=1, module="test", type="TestType")
    with pytest.raises(AttributeError, match="does not contain a legacy UUID"):
        _ = ref.uuid  # noqa


def test_serialized_component_reference_uuid_property_returns_value():
    """Test that SerializedComponentReference.uuid returns the legacy UUID."""
    from uuid import UUID
    from infrasys.serialization import SerializedComponentReference

    uuid_val = UUID("a1b2c3d4-0000-0000-0000-000000000001")
    ref = SerializedComponentReference(id=1, legacy_uuid=uuid_val, module="test", type="TestType")
    assert ref.uuid == uuid_val


def test_get_class_and_name_from_label_with_uuid():
    """Test that get_class_and_name_from_label parses UUID labels correctly."""
    from infrasys.models import get_class_and_name_from_label
    from uuid import UUID

    uuid_str = "a1b2c3d4-0000-0000-0000-000000000001"
    class_name, name = get_class_and_name_from_label(f"SimpleBus.{uuid_str}")
    assert class_name == "SimpleBus"
    assert isinstance(name, UUID)
    assert str(name) == uuid_str


def test_get_class_and_name_from_label_with_unknown_string():
    """Test that get_class_and_name_from_label falls back to string for unknown formats."""
    from infrasys.models import get_class_and_name_from_label

    class_name, name = get_class_and_name_from_label("Type.my-component")
    assert class_name == "Type"
    assert isinstance(name, str)
    assert name == "my-component"


def test_upgrade_legacy_component_ids_migration():
    """Test that upgrade_legacy_component_ids correctly upgrades a legacy UUID-based JSON."""
    from infrasys.utils.migrations import upgrade_legacy_component_ids

    # Simulate data after migrate_component_metadata has flattened __metadata__
    # (modern flat format: serialized_type at top level of metadata)
    system_data = {
        "components": [
            {
                "uuid": "a1b2c3d4-0000-0000-0000-000000000001",
                "name": "bus1",
                "voltage": 1.1,
                "__metadata__": {
                    "module": "tests.models.simple_system",
                    "type": "SimpleBus",
                    "serialized_type": "base",
                },
            },
            {
                "uuid": "a1b2c3d4-0000-0000-0000-000000000002",
                "name": "gen1",
                "active_power": 1.0,
                "__metadata__": {
                    "module": "tests.models.simple_system",
                    "type": "SimpleGenerator",
                    "serialized_type": "base",
                },
                "bus": {
                    "__metadata__": {
                        "module": "tests.models.simple_system",
                        "type": "SimpleBus",
                        "serialized_type": "composed_component",
                        "uuid": "a1b2c3d4-0000-0000-0000-000000000001",
                    }
                },
            },
        ],
        "supplemental_attributes": [],
    }

    upgrade_legacy_component_ids(system_data)

    # All components should have integer IDs
    components = system_data["components"]
    assert all(isinstance(c["id"], int) for c in components)
    assert components[0]["id"] == 1
    assert components[1]["id"] == 2

    # UUID field should be replaced by legacy_uuid
    assert "uuid" not in components[0]
    assert components[0]["legacy_uuid"] == "a1b2c3d4-0000-0000-0000-000000000001"
    assert "uuid" not in components[1]
    assert components[1]["legacy_uuid"] == "a1b2c3d4-0000-0000-0000-000000000002"

    # Composed component reference should have integer ID instead of UUID
    bus_metadata = components[1]["bus"]["__metadata__"]
    assert bus_metadata["id"] == 1
    assert "uuid" not in bus_metadata
    assert bus_metadata["legacy_uuid"] == "a1b2c3d4-0000-0000-0000-000000000001"
