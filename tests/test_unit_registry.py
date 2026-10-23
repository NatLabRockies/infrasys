"""Unit annotations and Pint quantities share an independently usable registry."""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel

from infrasys import BaseQuantity, Component, HasUnits, System, Unit, ureg
from infrasys.base_quantity import ureg as quantity_registry
from infrasys.quantities import ActivePower
from infrasys.units.conversion import ureg as conversion_registry


class PowerLimits(BaseModel):
    minimum: float
    maximum: float


class UnitDevice(HasUnits, Component):
    base_power: Annotated[float, Unit("MW")]
    power: Annotated[float, Unit("pu", base="base_power")]
    limits: Annotated[PowerLimits, Unit("MW", base="base_power")]
    measured_power: ActivePower


def test_annotations_and_quantities_share_registry() -> None:
    assert ureg is quantity_registry is conversion_registry is BaseQuantity._REGISTRY
    quantity = ureg.Quantity(1, "MW") + ActivePower(1000, "kW")
    assert quantity.to("MW").magnitude == 2


def test_system_round_trip_preserves_both_unit_representations(tmp_path: Path) -> None:
    device = UnitDevice(
        name="device",
        base_power=100.0,
        power={"value": 50000.0, "unit": "kW"},
        limits={"minimum": 10.0, "maximum": 80.0},
        measured_power=ureg.Quantity(5000, "kW"),
    )
    assert device.power == 0.5
    assert device.limits.maximum == 0.8
    assert device.model_dump()["limits"]["unit"] == "pu"
    assert device.measured_power.to_dict() == {"value": 5000, "units": "kilowatt"}

    with System(name="units") as system:
        system.add_component(device)
        path = tmp_path / "units.json"
        system.to_json(path)

    with System.from_json(path) as restored:
        loaded = restored.get_component(UnitDevice, "device")
        assert loaded.power == 0.5
        assert loaded.limits.minimum == 0.1
        assert loaded.limits.maximum == 0.8
        assert loaded.measured_power.to("MW").magnitude == 5
