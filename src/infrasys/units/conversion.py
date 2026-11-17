"""Unit conversion and model representation formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING, SupportsFloat

import pint

from infrasys.base_quantity import ureg

from .schema import PER_UNIT_UNIT, UnitInput
from .validation import (
    parse_unit_input,
    reject_boolean_magnitude,
    validate_conversion_base,
    validate_system_base,
)

if TYPE_CHECKING:
    from . import UnitSystem
    from .specs import UnitSpec


def convert_to_internal(
    value: int | float | UnitInput[SupportsFloat | str, str],
    *,
    spec: UnitSpec,
    base_value: float | None = None,
    base_unit: str | None = None,
) -> float:
    """Convert an input value to its internal representation.

    Parameters
    ----------
    value : int, float, or UnitInput
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
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    else:
        reject_boolean_magnitude(value)
        result = convert_unit_input(value, spec=spec, base_value=base_value, base_unit=base_unit)
    return result


def convert_unit_input(
    value: object,
    *,
    spec: UnitSpec,
    base_value: object | None,
    base_unit: str | None,
) -> float:
    """Convert one unit mapping to the model's internal value."""
    input_value, input_unit = parse_unit_input(value)

    if spec.unit == PER_UNIT_UNIT:
        if input_unit == PER_UNIT_UNIT:
            return input_value
        if spec.base is None:
            msg = "Per-unit conversion requires a declared base field"
            raise ValueError(msg)

    if spec.base is not None:
        base_value, base_unit = validate_conversion_base(
            spec, base_value=base_value, base_unit=base_unit
        )
        target_unit = base_unit if spec.unit == PER_UNIT_UNIT else spec.unit
    else:
        base_value = None
        base_unit = None
        target_unit = spec.unit
    return convert_unit_magnitude(
        input_value,
        input_unit,
        target_unit,
        spec=spec,
        base_value=base_value,
        base_unit=base_unit,
    )


def convert_unit_magnitude(
    input_value: float,
    input_unit: str,
    target_unit: str,
    *,
    spec: UnitSpec,
    base_value: float | None,
    base_unit: str | None,
) -> float:
    """Use Pint to normalize a parsed magnitude and apply its device base."""
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
    """Format an internal value for the selected unit representation.

    Parameters
    ----------
    value : float
        Internal value, using per-unit storage for relative quantities.
    spec : UnitSpec
        Unit metadata for the field.
    unit_system : UnitSystem
        Representation strategy for the model value.
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

    Raises
    ------
    ValueError
        If ``system_base`` is nonnumeric, non-finite, or zero.
    """
    from . import UnitSystem

    if spec.base is None:
        return f"{value} {spec.unit}"

    if unit_system == UnitSystem.DEVICE_BASE:
        return f"{value} {PER_UNIT_UNIT}"

    if unit_system == UnitSystem.NATURAL_UNITS:
        if base_value is None or base_unit is None:
            return f"{value} {PER_UNIT_UNIT}"
        natural_value = value * base_value
        return f"{natural_value:.4g} {base_unit}"

    if unit_system == UnitSystem.SYSTEM_BASE:
        if base_value is None:
            return f"{value} {PER_UNIT_UNIT}"
        natural_value = value * base_value
        if system_base is not None:
            validate_system_base(system_base)
            system_pu = natural_value / system_base
            return f"{system_pu:.4g} {PER_UNIT_UNIT} (system)"
        return f"{value} {PER_UNIT_UNIT}"

    return f"{value} {PER_UNIT_UNIT}"
