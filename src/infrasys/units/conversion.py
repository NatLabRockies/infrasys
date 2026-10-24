"""Unit conversion and display formatting."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pint

from infrasys.base_quantity import ureg

if TYPE_CHECKING:
    from . import UnitSystem
    from .specs import UnitSpec

__all__ = ["convert_to_internal", "format_for_display", "ureg"]


def convert_to_internal(
    value: object,
    *,
    spec: UnitSpec,
    base_value: float | None = None,
    base_unit: str | None = None,
) -> float:
    """Convert an input value to its internal representation.

    Parameters
    ----------
    value : float, int, or dict
        Input value. Dict inputs contain ``value`` and ``unit`` keys.
    spec : UnitSpec
        Unit metadata for the field.
    base_value : float or None, optional
        Base quantity value used for per-unit conversion.
    base_unit : str or None, optional
        Unit string declared by the base field.

    Returns
    -------
    float
        Value in internal representation.

    Raises
    ------
    ValueError
        If input is malformed, units are unknown or incompatible, or a required
        base is missing, zero, or non-finite.
    """
    if isinstance(value, int | float):
        return float(value)

    if not isinstance(value, dict) or "value" not in value or "unit" not in value:
        msg = "Expected a number or a mapping with 'value' and 'unit' keys"
        raise ValueError(msg)

    input_value = _parse_input_magnitude(value["value"])
    input_unit = _validate_input_unit(value["unit"])

    if spec.unit == "pu":
        if input_unit == "pu":
            return input_value
        if spec.base is None:
            msg = "Per-unit conversion requires a declared base field"
            raise ValueError(msg)

    target_unit = base_unit if spec.unit == "pu" else spec.unit
    if spec.base is not None:
        _validate_conversion_base(spec, base_value=base_value, base_unit=base_unit)

    try:
        normalized = ureg.Quantity(input_value, input_unit).to(target_unit)
        if spec.base is None:
            return float(normalized.magnitude)
        relative = (normalized / ureg.Quantity(base_value, base_unit)).to_reduced_units()
        return float(relative.magnitude)
    except pint.UndefinedUnitError as error:
        msg = f"Unknown unit in conversion: {error}"
        raise ValueError(msg) from error
    except pint.DimensionalityError as error:
        msg = f"Unit '{input_unit}' is incompatible with '{target_unit}'"
        raise ValueError(msg) from error


def format_for_display(
    value: float,
    *,
    spec: UnitSpec,
    unit_system: UnitSystem,
    base_value: float | None = None,
    base_unit: str | None = None,
    system_base: float | None = None,
) -> str:
    """Format an internal value according to the selected display mode.

    Parameters
    ----------
    value : float
        Internal value, using per-unit storage for relative quantities.
    spec : UnitSpec
        Unit metadata for the field.
    unit_system : UnitSystem
        Active display mode.
    base_value : float or None, optional
        Base field value for natural-unit conversion.
    base_unit : str or None, optional
        Unit declared by the base field.
    system_base : float or None, optional
        System-wide base used for system-base display.

    Returns
    -------
    str
        Value formatted with its display unit.
    """
    from . import UnitSystem

    if spec.base is None:
        return f"{value} {spec.unit}"

    if unit_system == UnitSystem.DEVICE_BASE:
        return f"{value} pu"

    if unit_system == UnitSystem.NATURAL_UNITS:
        if base_value is None or base_unit is None:
            return f"{value} pu"
        natural_value = value * base_value
        return f"{natural_value:.4g} {base_unit}"

    if unit_system == UnitSystem.SYSTEM_BASE:
        if base_value is None:
            return f"{value} pu"
        natural_value = value * base_value
        if system_base is not None and isinstance(system_base, int | float):
            system_pu = natural_value / system_base
            return f"{system_pu:.4g} pu (system)"
        return f"{value} pu"

    return f"{value} pu"


def _validate_conversion_base(
    spec: UnitSpec, *, base_value: float | None, base_unit: str | None
) -> None:
    """Require a usable, explicitly annotated base for relative conversion."""
    if base_value is None:
        msg = f"Base field '{spec.base}' is required for unit conversion but was not provided or is None"
        raise ValueError(msg)
    if base_unit is None:
        msg = (
            f"Base unit for field '{spec.base}' could not be determined - check field annotations"
        )
        raise ValueError(msg)
    if not math.isfinite(base_value) or base_value == 0:
        msg = f"Base field '{spec.base}' must be finite and non-zero for per-unit conversion"
        raise ValueError(msg)


def _validate_input_unit(input_unit: object) -> str:
    """Return a nonempty unit name or reject malformed envelope input."""
    if not isinstance(input_unit, str) or not input_unit:
        msg = "Unit mapping 'unit' must be a non-empty string"
        raise ValueError(msg)
    return input_unit


def _parse_input_magnitude(value: Any) -> float:
    """Normalize envelope magnitudes using Python's numeric input contract."""
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        msg = "Unit mapping 'value' must be numeric"
        raise ValueError(msg) from error
