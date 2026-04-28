from abc import ABC
from enum import StrEnum

from pydantic import Field
from typing_extensions import Annotated

from infrasys.device_parameter import DeviceParameter
from infrasys.models import InfraSysBaseModel
from infrasys.value_curves import AverageRateCurve, IncrementalCurve, InputOutputCurve, LinearCurve


class UnitSystem(StrEnum):
    SYSTEM_BASE = "SYSTEM_BASE"
    DEVICE_BASE = "DEVICE_BASE"
    NATURAL_UNITS = "NATURAL_UNITS"


class ProductionVariableCostCurve(InfraSysBaseModel):
    """Abstract class ValueCurves."""

    power_units: UnitSystem
    value_curve: Annotated[
        InputOutputCurve | IncrementalCurve | AverageRateCurve,
        Field(
            description="The underlying `ValueCurve` representation of this `ProductionVariableCostCurve`"
        ),
    ]
    vom_cost: Annotated[
        InputOutputCurve,
        Field(description="(default: natural units (MW)) The units for the x-axis of the curve"),
    ] = LinearCurve(0.0)


class CostCurve(ProductionVariableCostCurve):
    """Direct representation of the variable operation cost of a power plant in currency.

    Composed of a Value Curve that may represent input-output, incremental, or average rate
    data. The default units for the x-axis are MW and can be specified with
    `power_units`.
    """

    ...


class FuelCurve(ProductionVariableCostCurve):
    """Representation of the variable operation cost of a power plant in terms of fuel.

    Fuel units (MBTU, liters, m^3, etc.) coupled with a conversion factor between fuel and currency.
    Composed of a Value Curve that may represent input-output, incremental, or average rate data.
    The default units for the x-axis are MW and can be specified with `power_units`.
    """

    fuel_cost: Annotated[
        float,
        Field(
            description="Either a fixed value for fuel cost or the key to a fuel cost time series"
        ),
    ] = 0.0

    startup_fuel_offtake: Annotated[
        InputOutputCurve,
        Field(
            description="Fuel consumption at the unit startup procedure. Additional cost to the startup costs and related only to the initial fuel required to start the unit."
        ),
    ] = LinearCurve(0.0)


class OperationalCost(DeviceParameter, ABC):
    """Abstract supertype for operational cost representations."""


class OfferCurveCost(OperationalCost, ABC):
    """Abstract base type for market offer-curve operational costs."""
