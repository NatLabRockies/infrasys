"""Test the HasUnits vs HasPerUnit class hierarchy."""

from typing import Annotated

import pytest
from infrasys import Component
from pydantic import BaseModel, model_validator

from infrasys.units import HasPerUnit, HasUnits, Unit, UnitSystem, set_unit_system


class Sensor(HasUnits, Component):
    """A sensor that only reports absolute values."""

    name: str
    temperature: Annotated[float, Unit("°C")]
    pressure: Annotated[float, Unit("Pa")]


class Generator(HasPerUnit, Component):
    """A generator with base values and per-unit fields."""

    name: str
    base_power: Annotated[float, Unit("MVA")]
    rated_voltage: Annotated[float, Unit("kV")]
    rating: Annotated[float, Unit("pu", base="base_power")]
    voltage: Annotated[float, Unit("pu", base="rated_voltage")]


def test_has_units_with_absolute_values() -> None:
    """Test HasUnits with only absolute unit fields."""
    sensor = Sensor(name="TempSensor1", temperature=25.0, pressure=101325.0)
    assert sensor.temperature == 25.0
    assert sensor.pressure == 101325.0


def test_has_units_repr_formatting() -> None:
    """Test HasUnits repr includes unit labels."""
    sensor = Sensor(name="TempSensor1", temperature=25.0, pressure=101325.0)
    repr_str = repr(sensor)
    assert "25.0 °C" in repr_str
    assert "101325.0 Pa" in repr_str


def test_has_per_units_with_pu_input() -> None:
    """Test HasPerUnit with per-unit input values."""
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    assert gen.rating == 0.8
    assert gen.voltage == 1.0


def test_has_per_units_with_natural_unit_input() -> None:
    """Test HasPerUnit with natural unit dict input."""
    gen = Generator(
        name="Gen2",
        base_power=100.0,
        rated_voltage=13.8,
        rating={"value": 80.0, "unit": "MW"},  # type: ignore[arg-type]
        voltage={"value": 13.8, "unit": "kV"},  # type: ignore[arg-type]
    )
    assert gen.rating == pytest.approx(0.8, abs=1e-6)
    assert gen.voltage == pytest.approx(1.0, abs=1e-6)


def test_has_per_units_repr_device_base() -> None:
    """Test HasPerUnit repr in device base mode."""
    set_unit_system(UnitSystem.DEVICE_BASE)
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    repr_str = repr(gen)
    assert "0.8 pu" in repr_str
    assert "1.0 pu" in repr_str


def test_has_per_units_repr_natural_units() -> None:
    """Test HasPerUnit repr in natural units mode."""
    set_unit_system(UnitSystem.NATURAL_UNITS)
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    repr_str = repr(gen)
    assert "80" in repr_str
    assert "MVA" in repr_str
    assert "13.8" in repr_str
    assert "kV" in repr_str
    set_unit_system(UnitSystem.DEVICE_BASE)


def test_class_hierarchy_isinstance_sensor() -> None:
    """Test isinstance checks for HasUnits sensor."""
    sensor = Sensor(name="TempSensor1", temperature=25.0, pressure=101325.0)
    assert isinstance(sensor, HasUnits)
    assert not isinstance(sensor, HasPerUnit)


def test_class_hierarchy_isinstance_generator() -> None:
    """Test isinstance checks for HasPerUnit generator."""
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    assert isinstance(gen, HasUnits)
    assert isinstance(gen, HasPerUnit)


def test_has_units_system_base_is_none() -> None:
    """HasUnits models without system-base support return None."""
    sensor = Sensor(name="TempSensor1", temperature=25.0, pressure=101325.0)
    assert sensor.system_base is None


def test_has_per_units_system_base_can_be_set() -> None:
    """Set the system base through the public binding method."""
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    gen.bind_system_base(150.0)
    assert gen.system_base == 150.0


def test_system_base_binding_is_instance_local_and_excluded_from_serialization() -> None:
    first = Generator(name="first", base_power=100.0, rated_voltage=13.8, rating=0.8, voltage=1.0)
    second = Generator(
        name="second", base_power=100.0, rated_voltage=13.8, rating=0.8, voltage=1.0
    )
    fields = set(first.model_dump())

    first.bind_system_base(150.0)

    assert first.system_base == 150.0
    assert second.system_base is None
    assert set(first.model_dump()) == fields
    assert "system_base" not in first.model_dump_json()
    assert "system_base" not in Generator.model_json_schema()["properties"]


def test_has_per_units_system_base_repr() -> None:
    """Test HasPerUnit repr with system base set."""
    set_unit_system(UnitSystem.SYSTEM_BASE)
    gen = Generator(
        name="Gen1",
        base_power=100.0,
        rated_voltage=13.8,
        rating=0.8,
        voltage=1.0,
    )
    gen.bind_system_base(150.0)
    repr_str = repr(gen)
    assert "pu (system)" in repr_str
    set_unit_system(UnitSystem.DEVICE_BASE)


def test_mixed_units_conversion() -> None:
    """Test conversion with mixed natural and pu inputs."""
    gen = Generator(
        name="Gen3",
        base_power=100.0,
        rated_voltage=13.8,
        rating={"value": 90.0, "unit": "MVA"},  # type: ignore[arg-type]
        voltage=1.05,
    )
    assert gen.rating == pytest.approx(0.9, abs=1e-6)
    assert gen.voltage == 1.05


def test_unit_specs_contains_exactly_annotated_fields() -> None:
    """The class-level registry contains only fields with Unit metadata."""
    assert set(Sensor.unit_specs) == {"temperature", "pressure"}
    assert set(Generator.unit_specs) == {"base_power", "rated_voltage", "rating", "voltage"}
    assert Generator.unit_specs["rating"].base == "base_power"
    assert Generator.unit_specs["base_power"].unit == "MVA"


def test_same_named_classes_resolve_their_own_base_units() -> None:
    """Class-name collisions do not affect per-unit conversion."""

    def make_generator(base_unit: str) -> type[Component]:
        class Generator(HasUnits, Component):
            base_power: Annotated[float, Unit(base_unit)]
            rating: Annotated[float, Unit("pu", base="base_power")]

        return Generator

    mva_generator = make_generator("MVA")
    kva_generator = make_generator("kVA")

    mva_instance = mva_generator(
        name="MVA generator",
        base_power=100.0,
        rating={"value": 80.0, "unit": "MVA"},  # type: ignore[arg-type]
    )
    kva_instance = kva_generator(
        name="kVA generator",
        base_power=100.0,
        rating={"value": 80.0, "unit": "kVA"},  # type: ignore[arg-type]
    )

    assert mva_instance.rating == pytest.approx(0.8)
    assert kva_instance.rating == pytest.approx(0.8)


def test_subclasses_can_override_base_units_without_changing_parent_conversion() -> None:
    class Parent(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MVA")]
        rating: Annotated[float, Unit("pu", base="base_power")]

    class Child(Parent):
        base_power: Annotated[float, Unit("kVA")]

    parent = Parent.model_validate({"base_power": 100.0, "rating": {"value": 80.0, "unit": "MVA"}})
    child = Child.model_validate({"base_power": 100.0, "rating": {"value": 80.0, "unit": "kVA"}})

    assert parent.rating == pytest.approx(0.8)
    assert child.rating == pytest.approx(0.8)
    assert Parent.unit_specs["base_power"].unit == "MVA"
    assert Child.unit_specs["base_power"].unit == "kVA"


def test_reusable_unit_annotations_resolve_each_models_base_unit() -> None:
    per_unit = Annotated[float, Unit("pu", base="base_power")]

    class MegavoltDevice(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MVA")]
        rating: per_unit

    class KilovoltDevice(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("kVA")]
        rating: per_unit

    mega = MegavoltDevice.model_validate(
        {"base_power": 100.0, "rating": {"value": 80.0, "unit": "MVA"}}
    )
    kilo = KilovoltDevice.model_validate(
        {"base_power": 100.0, "rating": {"value": 80.0, "unit": "kVA"}}
    )

    assert mega.rating == pytest.approx(0.8)
    assert kilo.rating == pytest.approx(0.8)


@pytest.mark.parametrize("explicit_rebuild", [False, True])
def test_deferred_unit_annotations_convert_after_model_completion(explicit_rebuild: bool) -> None:
    class DeferredDevice(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MVA")]
        rating: "Annotated[deferred_rating, Unit('pu', base='base_power')]"

    deferred_rating = float
    if explicit_rebuild:
        DeferredDevice.model_rebuild()
    device = DeferredDevice.model_validate(
        {"base_power": 100.0, "rating": {"value": 80.0, "unit": "MVA"}}
    )

    assert device.rating == pytest.approx(0.8)
    assert set(DeferredDevice.unit_specs) == {"base_power", "rating"}
    assert "0.8 pu" in repr(device)


def test_nested_models_keep_their_own_base_units() -> None:
    class Inner(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("kW")]
        rating: Annotated[float, Unit("pu", base="base_power")]

    class Outer(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MW")]
        rating: Annotated[float, Unit("pu", base="base_power")]
        inner: Inner

    device = Outer.model_validate(
        {
            "base_power": 100.0,
            "rating": {"value": 5000.0, "unit": "kW"},
            "inner": {"base_power": 100.0, "rating": {"value": 50.0, "unit": "kW"}},
        }
    )
    assert device.rating == 0.05
    assert device.inner.rating == 0.5
    assert Outer.model_validate_json(device.model_dump_json()).inner.rating == 0.5


def test_unit_binding_preserves_multiple_model_validators() -> None:
    class Device(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MW")]
        rating: Annotated[float, Unit("pu", base="base_power")]

        @model_validator(mode="before")
        @classmethod
        def require_data(cls, value: object) -> object:
            if not isinstance(value, dict):
                msg = "Device input must be a mapping"
                raise ValueError(msg)
            return value

        @model_validator(mode="before")
        @classmethod
        def supply_base(cls, value: dict[str, object]) -> dict[str, object]:
            return {"base_power": 100.0, **value}

    device = Device.model_validate({"rating": {"value": 50.0, "unit": "MW"}})
    assert device.rating == 0.5


def test_missing_base_unit_field_raises_at_class_creation() -> None:
    """Reject a base reference that does not name a unit-annotated field."""
    with pytest.raises(TypeError, match="missing_base"):

        class InvalidComponent(HasUnits, Component):
            rating: Annotated[float, Unit("pu", base="missing_base")]


@pytest.mark.parametrize("schema_type", ["definitions", "model"])
@pytest.mark.parametrize("nested", [False, True])
def test_dictionary_defaults_are_not_traversed_as_core_schemas(
    schema_type: str, nested: bool
) -> None:
    default = {"type": schema_type}
    default_settings = {"items": [default]} if nested else default

    class Device(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MW")] = 100.0
        rating: Annotated[float, Unit("pu", base="base_power")]
        settings: dict[str, object] = default_settings

    device = Device.model_validate({"rating": {"value": 50.0, "unit": "MW"}})
    assert device.rating == 0.5
    assert device.settings == default_settings
    Device.model_rebuild(force=True)
    assert Device.model_validate_json(device.model_dump_json()).settings == default_settings
