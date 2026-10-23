"""Tests for the UnitSpec class."""

import json
from dataclasses import FrozenInstanceError
from typing import Annotated

import pytest
from infrasys import Component
from pydantic import BaseModel, ValidationError

from infrasys.units import HasUnits, Unit
from infrasys.units.specs import UnitSpec


def test_unitspec_dict_input_no_base():
    """Test UnitSpec validation with dict input when base is None."""

    class SimpleModel(BaseModel):
        """Model with unit field but no base."""

        power: Annotated[float, Unit("MW")]

    model = SimpleModel(power={"value": 100.0, "unit": "MW"})  # type: ignore[arg-type]
    assert model.power == 100.0


def test_unitspec_dict_input_with_conversion():
    """Test UnitSpec validation with dict input and unit conversion."""

    class Generator(HasUnits, Component):
        """Generator with units."""

        base_power: Annotated[float, Unit("MVA")]
        rating: Annotated[float, Unit("pu", base="base_power")]

    gen = Generator(name="Gen1", base_power=100.0, rating={"value": 80.0, "unit": "MVA"})  # type: ignore[arg-type]
    assert gen.rating == 0.8


@pytest.mark.parametrize("json_input", [False, True])
def test_scalar_units_preserve_optional_types_and_natural_conversion(json_input: bool) -> None:
    class Device(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MW")]
        rating: Annotated[float, Unit("pu", base="base_power")]
        optional_rating: Annotated[float | None, Unit("pu", base="base_power")]

    data = {
        "base_power": 100.0,
        "rating": {"value": 5000.0, "unit": "kW"},
        "optional_rating": None,
    }
    device = (
        Device.model_validate_json(json.dumps(data)) if json_input else Device.model_validate(data)
    )
    assert device.rating == 0.05
    assert device.optional_rating is None
    assert Device.model_validate_json(device.model_dump_json()).optional_rating is None

    data["rating"] = None
    with pytest.raises(ValidationError):
        if json_input:
            Device.model_validate_json(json.dumps(data))
        else:
            Device.model_validate(data)


def test_unit_annotations_are_immutable() -> None:
    spec = UnitSpec(unit="pu", base="base_power")

    with pytest.raises(FrozenInstanceError):
        spec.base = "other_base"


def test_unitspec_invalid_input_type():
    """Test UnitSpec validation raises ValidationError for invalid input types."""

    class TestModel(BaseModel):
        """Test model."""

        value: Annotated[float, Unit("MVA")]

    with pytest.raises(ValidationError):
        TestModel(value="invalid")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        TestModel(value=[1, 2, 3])  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        TestModel(value={"invalid": "dict"})  # type: ignore[arg-type]


def test_structured_unit_json_schema_describes_serialized_values() -> None:
    class Limits(BaseModel):
        minimum: float
        maximum: float

    class Device(HasUnits, BaseModel):
        base_power: Annotated[float, Unit("MW")]
        limits: Annotated[Limits, Unit("MW", base="base_power")]

    schema = Device.model_json_schema(mode="serialization")
    limits_schema = schema["properties"]["limits"]
    assert limits_schema["type"] == "object"
    assert set(limits_schema["required"]) == {"value", "unit"}
    assert limits_schema["properties"]["unit"]["const"] == "pu"
    assert limits_schema["properties"]["value"]["$ref"] == "#/$defs/Limits"
    assert schema["$defs"]["Limits"]["properties"]["minimum"]["type"] == "number"


def test_unitspec_validation_and_serialization_schemas():
    """Advertise scalar or envelope input and scalar output for unit fields."""

    class Generator(BaseModel):
        """Generator model."""

        name: str
        base_power: Annotated[float, Unit("MVA")]
        rating: Annotated[float, Unit("pu", base="base_power")]

    validation_schema = Generator.model_json_schema(mode="validation")
    for field_name in ("base_power", "rating"):
        field_schema = validation_schema["properties"][field_name]
        assert {"type": "number"} in field_schema["anyOf"]
        assert any(branch.get("type") == "object" for branch in field_schema["anyOf"])

    serialization_schema = Generator.model_json_schema(mode="serialization")
    assert serialization_schema["properties"]["base_power"]["type"] == "number"
    assert serialization_schema["properties"]["rating"]["type"] == "number"


def test_unitspec_no_base_field():
    """Test UnitSpec with no base field (simple unit)."""

    class SimpleModel(BaseModel):
        """Model with simple unit field."""

        voltage: Annotated[float, Unit("kV")]

    model1 = SimpleModel(voltage=13.8)
    assert model1.voltage == 13.8

    model2 = SimpleModel(voltage={"value": 13.8, "unit": "kV"})  # type: ignore[arg-type]
    assert model2.voltage == 13.8

    model3 = SimpleModel(voltage={"value": 13800.0, "unit": "V"})  # type: ignore[arg-type]
    assert model3.voltage == 13.8
