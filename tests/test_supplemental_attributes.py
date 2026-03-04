from datetime import datetime, timedelta

import pytest

from infrasys import GeographicInfo, SingleTimeSeries, SupplementalAttribute
from infrasys.exceptions import ISAlreadyAttached, ISNotStored, ISOperationNotAllowed
from infrasys.quantities import Energy
from infrasys.system import System

from .models.simple_system import (
    SimpleBus,
    SimpleGenerator,
    SimpleSystem,
)


class Attribute(SupplementalAttribute):
    energy: Energy


def test_supplemental_attribute_manager(tmp_path):
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = GeographicInfo.example()
    attr2 = GeographicInfo.example()
    attr2.geo_json["geometry"]["coordinates"] = [1.0, 2.0]
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)
    system.add_supplemental_attribute(bus, attr1)
    system.add_supplemental_attribute(bus, attr2)

    assert system.has_supplemental_attribute(bus)
    assert system.has_supplemental_attribute(bus, supplemental_attribute_type=GeographicInfo)
    assert system.has_supplemental_attribute_association(bus, attr1)
    assert system.has_supplemental_attribute_association(bus, attr2)
    assert system.get_num_supplemental_attributes() == 2
    assert system.get_num_components_with_supplemental_attributes() == 1
    counts_by_type = system.get_supplemental_attribute_counts_by_type()
    assert len(counts_by_type) == 1
    assert counts_by_type[0]["type"] == "GeographicInfo"
    assert counts_by_type[0]["count"] == 2

    with pytest.raises(ISAlreadyAttached):
        system.add_supplemental_attribute(bus, attr1)

    def check_attrs(attrs):
        assert len(attrs) == 2
        assert attrs[0] == attr1 or attrs[0] == attr2
        assert attrs[1] == attr1 or attrs[1] == attr2

    for attr_type in (GeographicInfo, SupplementalAttribute):
        attrs = list(system.get_supplemental_attributes(attr_type))
        check_attrs(attrs)

    assert system.get_supplemental_attribute_by_uuid(attr1.uuid) is attr1

    components = system.get_components_with_supplemental_attribute(attr1)
    assert len(components) == 1
    assert components[0] is bus

    attrs = system.get_supplemental_attributes_with_component(bus)
    check_attrs(attrs)

    with pytest.raises(ISOperationNotAllowed):
        system.get_supplemental_attributes_with_component(
            bus, supplemental_attribute_type=SupplementalAttribute
        )

    attrs = list(
        system.get_supplemental_attributes(
            GeographicInfo,
            filter_func=lambda x: x.geo_json["geometry"]["coordinates"] == [1.0, 2.0],
        )
    )
    assert len(attrs) == 1
    assert attrs[0] == attr2

    attrs = system.get_supplemental_attributes_with_component(
        bus,
        supplemental_attribute_type=GeographicInfo,
        filter_func=lambda x: x.geo_json["geometry"]["coordinates"] == [1.0, 2.0],
    )
    assert len(attrs) == 1
    assert attrs[0] == attr2

    path = tmp_path / "system"
    system.save(path)
    system_file = path / "system.json"
    assert system_file.exists()

    system.remove_supplemental_attribute(attr1)
    system.remove_supplemental_attribute(attr2)
    assert not system.get_supplemental_attributes_with_component(bus)
    for attr_type in (GeographicInfo, SupplementalAttribute):
        assert not list(system.get_supplemental_attributes(attr_type))
    assert not system.has_supplemental_attribute(bus)
    assert not system.has_supplemental_attribute(bus, supplemental_attribute_type=GeographicInfo)

    system2 = SimpleSystem.from_json(system_file)
    attrs = list(system2.get_supplemental_attributes(GeographicInfo))
    check_attrs(attrs)


def test_supplemental_attribute_removals():
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = GeographicInfo.example()
    attr2 = GeographicInfo.example()
    attr2.geo_json["geometry"]["coordinates"] = [1.0, 2.0]
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)
    system.add_supplemental_attribute(bus, attr1)
    system.add_supplemental_attribute(bus, attr2)
    system.remove_supplemental_attribute_from_component(bus, attr1)
    assert system.has_supplemental_attribute(bus, supplemental_attribute_type=GeographicInfo)
    system.remove_supplemental_attribute_from_component(bus, attr2)
    assert not list(system.get_supplemental_attributes(GeographicInfo))
    with pytest.raises(ISNotStored):
        system.get_supplemental_attribute_by_uuid(attr1.uuid)
    with pytest.raises(ISNotStored):
        system.remove_supplemental_attribute(attr1)
    with pytest.raises(ISNotStored):
        system.remove_supplemental_attribute_from_component(bus, attr1)


def test_one_attribute_many_components():
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    gen2 = SimpleGenerator(name="gen2", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = GeographicInfo.example()
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)
    system.add_component(gen2)
    system.add_supplemental_attribute(gen, attr1)
    system.add_supplemental_attribute(gen2, attr1)


def test_attribute_with_basequantity(tmp_path):
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = Attribute(energy=Energy(10.0, "kWh"))
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)
    system.add_supplemental_attribute(gen, attr1)
    system.to_json(tmp_path / "test.json")
    system2 = System.from_json(tmp_path / "test.json")

    gen2 = system2.get_component(SimpleGenerator, "gen1")
    attr2: Attribute = system.get_supplemental_attributes_with_component(gen2)[0]
    assert attr1 == attr2


def test_supplemental_attributes_with_time_series():
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = Attribute(energy=Energy(10.0, "kWh"))
    system = SimpleSystem(auto_add_composed_components=True)
    data = range(100)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(data, "active_power", start, resolution)
    system.add_component(gen)
    system.add_supplemental_attribute(gen, attr1)
    system.add_time_series(ts, attr1)

    # Assert that we can run this
    system.info()


def test_add_supplemental_attribute_with_connection_context():
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = GeographicInfo.example()
    attr2 = GeographicInfo.example()
    attr2.geo_json["geometry"]["coordinates"] = [10.0, 20.0]
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)

    with system.open_supplemental_attribute_store() as conn:
        system.add_supplemental_attribute(bus, attr1, connection=conn)
        system.add_supplemental_attribute(bus, attr2, connection=conn)

    attrs = system.get_supplemental_attributes_with_component(bus)
    assert len(attrs) == 2
    assert system.get_num_supplemental_attributes() == 2
    assert system.get_num_components_with_supplemental_attributes() == 1


def test_add_supplemental_attribute_with_connection_context_rolls_back_on_error():
    bus = SimpleBus(name="test-bus", voltage=1.1)
    gen = SimpleGenerator(name="gen1", active_power=1.0, rating=1.0, bus=bus, available=True)
    attr1 = GeographicInfo.example()
    system = SimpleSystem(auto_add_composed_components=True)
    system.add_component(gen)

    with pytest.raises(ISAlreadyAttached):
        with system.open_supplemental_attribute_store() as conn:
            system.add_supplemental_attribute(bus, attr1, connection=conn)
            system.add_supplemental_attribute(bus, attr1, connection=conn)

    assert not system.get_supplemental_attributes_with_component(bus)
    assert system.get_num_supplemental_attributes() == 0
    assert system.get_num_components_with_supplemental_attributes() == 0
    with pytest.raises(ISNotStored):
        system.get_supplemental_attribute_by_uuid(attr1.uuid)
