from datetime import datetime, timedelta

import pytest
from loguru import logger

from infrasys.location import Location
from infrasys.quantities import Energy
from infrasys.time_series_models import NonSequentialTimeSeries, SingleTimeSeries

from .models.simple_system import SimpleBus, SimpleGenerator, SimpleSubsystem, SimpleSystem


@pytest.fixture
def simple_system() -> SimpleSystem:
    """Creates a system."""
    system = SimpleSystem(name="test-system")
    geo = Location(x=1.0, y=2.0)
    bus = SimpleBus(name="test-bus", voltage=1.1, coordinates=geo)
    gen = SimpleGenerator(name="test-gen", active_power=1.0, rating=1.0, bus=bus, available=True)
    subsystem = SimpleSubsystem(name="test-subsystem", generators=[gen])
    system.add_components(geo, bus, gen, subsystem)
    return system


@pytest.fixture
def simple_system_with_time_series(simple_system) -> SimpleSystem:
    """Creates a system with time series data."""
    variable_name = "active_power"
    length = 8784
    df = range(length)
    start = datetime(year=2020, month=1, day=1)
    resolution = timedelta(hours=1)
    ts = SingleTimeSeries.from_array(df, variable_name, start, resolution)
    gen = simple_system.get_component(SimpleGenerator, "test-gen")
    simple_system.add_time_series(ts, gen)
    return simple_system


@pytest.fixture
def simple_system_with_nonsequential_time_series(simple_system) -> SimpleSystem:
    """Creates a system with time series data."""
    variable_name = "active_power"
    length = 10
    df = range(length)
    timestamps = [
        datetime(year=2030, month=1, day=1) + timedelta(seconds=5 * i) for i in range(length)
    ]
    ts = NonSequentialTimeSeries.from_array(data=df, name=variable_name, timestamps=timestamps)
    gen = simple_system.get_component(SimpleGenerator, "test-gen")
    simple_system.add_time_series(ts, gen)
    return simple_system


@pytest.fixture
def simple_system_with_mixed_time_series(simple_system) -> SimpleSystem:
    """Creates a system with both SingleTimeSeries and NonSequentialTimeSeries."""
    gen = simple_system.get_component(SimpleGenerator, "test-gen")

    sts = SingleTimeSeries.from_array(
        range(100), "active_power", datetime(year=2020, month=1, day=1), timedelta(hours=1)
    )
    simple_system.add_time_series(sts, gen)

    nsts = NonSequentialTimeSeries.from_array(
        data=range(10),
        name="reactive_power",
        timestamps=[
            datetime(year=2030, month=1, day=1) + timedelta(seconds=5 * i) for i in range(10)
        ],
    )
    simple_system.add_time_series(nsts, gen)
    return simple_system


@pytest.fixture
def simple_system_with_supplemental_attributes(simple_system) -> SimpleSystem:
    """Creates a system with supplemental attributes."""
    from infrasys.location import GeographicInfo

    from .test_supplemental_attributes import Attribute

    bus = simple_system.get_component(SimpleBus, "test-bus")
    gen = simple_system.get_component(SimpleGenerator, "test-gen")

    attr1 = GeographicInfo.example()
    attr2 = GeographicInfo.example()
    attr2.geo_json["geometry"]["coordinates"] = [1.0, 2.0]

    attr3 = Attribute(energy=Energy(10.0, "kWh"))

    simple_system.add_supplemental_attribute(bus, attr1)
    simple_system.add_supplemental_attribute(bus, attr2)
    simple_system.add_supplemental_attribute(gen, attr3)

    return simple_system


@pytest.fixture
def caplog(caplog):
    """Enable logging for the package"""
    logger.remove()
    logger.enable("infrasys")
    handler_id = logger.add(caplog.handler)
    yield caplog
    logger.remove(handler_id)
