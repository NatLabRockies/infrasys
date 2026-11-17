"""Validation and parsing helpers for unit inputs and bases."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, SupportsFloat

from .schema import UNIT_KEY, VALUE_KEY

if TYPE_CHECKING:
    from .specs import UnitSpec


def reject_boolean_magnitude(value: object) -> None:
    """Reject booleans, which Python otherwise treats as numbers."""
    if isinstance(value, bool):
        msg = "Boolean values are not valid unit magnitudes"
        raise ValueError(msg)


def finite_nonzero_float(value: object) -> float | None:
    """Convert a numeric value to a finite, non-zero float when possible."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number != 0 else None


def validate_system_base(system_base: object) -> None:
    """Require a finite, non-zero numeric system base."""
    if finite_nonzero_float(system_base) is None:
        msg = "System base must be finite and non-zero"
        raise ValueError(msg)


def validate_structured_base(base_value: object | None, *, base_field: str) -> float:
    """Return a usable numeric base value for structured unit conversion."""
    if base_value is None:
        msg = f"Base value {base_field!r} is required for unit conversion"
        raise ValueError(msg)
    numeric_base = finite_nonzero_float(base_value)
    if numeric_base is None:
        msg = f"Base value {base_field!r} must be finite and non-zero for unit conversion"
        raise ValueError(msg)
    return numeric_base


def validate_conversion_base(
    spec: UnitSpec, *, base_value: object | None, base_unit: str | None
) -> tuple[float, str]:
    """Return validated base inputs or reject incomplete per-unit conversion."""
    if base_value is None:
        msg = f"Base field '{spec.base}' is required for unit conversion but was not provided or is None"
        raise ValueError(msg)
    if base_unit is None:
        msg = (
            f"Base unit for field '{spec.base}' could not be determined - check field annotations"
        )
        raise ValueError(msg)
    numeric_base = finite_nonzero_float(base_value)
    if numeric_base is None:
        msg = f"Base field '{spec.base}' must be finite and non-zero for per-unit conversion"
        raise ValueError(msg)
    return numeric_base, base_unit


def validate_input_unit(input_unit: object) -> str:
    """Return a nonempty unit name or reject a malformed unit mapping."""
    if not isinstance(input_unit, str) or not input_unit:
        msg = f"Unit mapping {UNIT_KEY!r} must be a non-empty string"
        raise ValueError(msg)
    return input_unit


def parse_input_magnitude(value: object) -> float:
    """Convert a supported unit-mapping magnitude to a float."""
    msg = f"Unit mapping {VALUE_KEY!r} must be numeric"
    reject_boolean_magnitude(value)
    if not isinstance(value, (SupportsFloat, str)):
        raise ValueError(msg)
    try:
        return float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(msg) from error


def parse_unit_input(value: object) -> tuple[float, str]:
    """Validate a unit mapping and return its magnitude and unit."""
    if not isinstance(value, dict) or VALUE_KEY not in value or UNIT_KEY not in value:
        msg = f"Expected a number or a mapping with {VALUE_KEY!r} and {UNIT_KEY!r} keys"
        raise ValueError(msg)
    return parse_input_magnitude(value[VALUE_KEY]), validate_input_unit(value[UNIT_KEY])
