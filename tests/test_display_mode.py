from typing import Annotated

import pytest
from infrasys import Component
from pydantic import BaseModel

from infrasys.units import HasPerUnit, HasUnits, Unit, UnitSystem, unit_system


class Demo(HasPerUnit, Component):
    name: str
    base_power: Annotated[float, Unit("MVA")]
    rating: Annotated[float, Unit("pu", base="base_power")]
    optional_rating: Annotated[float | None, Unit("pu", base="base_power")] = None


class DisplayFields(HasUnits, BaseModel):
    base_power: Annotated[float, Unit("MW")]
    rating: Annotated[float, Unit("pu", base="base_power")]
    optional_rating: Annotated[float | None, Unit("pu", base="base_power")] = None


class InheritedDisplayFields(DisplayFields):
    label: str = "inherited"


@pytest.mark.parametrize(
    "model_type",
    [
        pytest.param(DisplayFields, id="direct"),
        pytest.param(InheritedDisplayFields, id="inherited"),
    ],
)
@pytest.mark.parametrize(
    "mode", [UnitSystem.DEVICE_BASE, UnitSystem.SYSTEM_BASE, UnitSystem.NATURAL_UNITS]
)
def test_optional_unit_fields_remain_none_in_direct_and_inherited_models(
    model_type: type[DisplayFields], mode: UnitSystem
) -> None:
    model = model_type(base_power=100.0, rating=0.5)
    expected_rating = {
        UnitSystem.DEVICE_BASE: "0.5 pu",
        UnitSystem.SYSTEM_BASE: "0.5 pu",
        UnitSystem.NATURAL_UNITS: "50 MW",
    }[mode]

    with unit_system(mode):
        rendered = repr(model)

    assert "optional_rating=None" in rendered
    assert "base_power='100.0 MW'" in rendered
    assert f"rating='{expected_rating}'" in rendered


def test_optional_per_unit_field_remains_none_after_system_binding() -> None:
    device = Demo(name="demo", base_power=100.0, rating=0.5)
    device.bind_system_base(200.0)

    assert device.system_base == 200.0
    for mode, expected_rating in (
        (UnitSystem.DEVICE_BASE, "0.5 pu"),
        (UnitSystem.SYSTEM_BASE, "0.25 pu (system)"),
        (UnitSystem.NATURAL_UNITS, "50 MVA"),
    ):
        with unit_system(mode):
            rendered = repr(device)

        assert "optional_rating=None" in rendered
        assert "base_power='100.0 MVA'" in rendered
        assert f"rating='{expected_rating}'" in rendered


def test_unit_system_context_manager_restores() -> None:
    demo = Demo(name="demo", base_power=100.0, rating=0.8)
    assert "pu" in repr(demo)
    with unit_system(UnitSystem.NATURAL_UNITS):
        s = repr(demo)
        assert "MVA" in s or "100" in s
    assert "pu" in repr(demo)


def test_unit_system_context_manager_switch() -> None:
    demo = Demo(name="demo", base_power=100.0, rating=0.8)
    with unit_system(UnitSystem.SYSTEM_BASE):
        s = repr(demo)
        assert "system" in s or "pu" in s
    assert "pu" in repr(demo)


@pytest.mark.parametrize("hidden_unit", [False, True])
def test_unit_models_respect_fields_hidden_from_repr(hidden_unit: bool) -> None:
    from pydantic import Field

    hidden_type = Annotated[float, Unit("MW")] if hidden_unit else float

    class Device(HasUnits, BaseModel):
        secret: hidden_type = Field(repr=False)
        power: Annotated[float, Unit("MW")]

    device = Device(secret=123456789, power=1)
    assert "secret" not in repr(device)
    assert "123456789" not in repr(device)
    assert "1.0 MW" in repr(device)
    assert device.model_dump()["secret"] == 123456789


@pytest.mark.parametrize("mode", list(UnitSystem))
def test_structured_values_remain_representable_in_every_display_mode(mode: UnitSystem) -> None:
    class Limits(BaseModel):
        minimum: float

    class Device(HasPerUnit, BaseModel):
        base_power: Annotated[float, Unit("MW")]
        limits: Annotated[Limits, Unit("MW", base="base_power")]

    device = Device.model_validate({"base_power": 100.0, "limits": {"minimum": 50.0}})
    device.bind_system_base(200.0)
    with unit_system(mode):
        assert "Limits(minimum=0.5)" in repr(device)
    assert device.limits.minimum == 0.5
