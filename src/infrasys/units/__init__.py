"""Unit-aware fields and unit representations for power system models.

This module provides field annotations, natural-to-per-unit conversion, and
representation policies for unit-aware model values. A representation policy
changes ``__repr__`` output only. It does not change stored values or serialized
model data.

Notes
-----
* Annotate numeric fields with ``Annotated[float, Unit("pu", base="base_field")]``
* Natural-unit inputs (``{"value": 138, "unit": "kV"}``) are converted to per-unit when ``base`` is set
* Internal storage is always float (device-base per-unit for relative quantities)
* Unit representations (device base, system base, or natural units) affect ``__repr__`` only
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum

from .conversion import convert_to_internal, format_for_display, ureg
from .mixins import HasPerUnit, HasUnits
from .schema import UnitInput
from .specs import Unit, UnitSpec, unit_spec


class UnitSystem(StrEnum):
    """Unit representation strategies for unit-aware model values.

    The selected strategy controls how ``HasUnits`` values appear in model
    ``__repr__`` output. It does not alter internal values or serialization.

    Attributes
    ----------
    DEVICE_BASE : str
        Represent relative values in per-unit against each device's base.
    SYSTEM_BASE : str
        Represent relative values in per-unit against a bound system base.
    NATURAL_UNITS : str
        Represent relative values using their declared natural units.
    """

    DEVICE_BASE = "DEVICE_BASE"
    SYSTEM_BASE = "SYSTEM_BASE"
    NATURAL_UNITS = "NATURAL_UNITS"


current_unit_system: UnitSystem = UnitSystem.DEVICE_BASE


def get_unit_system() -> UnitSystem:
    """Get the current global unit representation strategy.

    Returns
    -------
    UnitSystem
        Current strategy for unit-aware model representations.
    """
    return current_unit_system


def set_unit_system(unit_system: UnitSystem) -> UnitSystem:
    """Set and return the active global unit representation strategy.

    Parameters
    ----------
    unit_system : UnitSystem
        Representation strategy to apply to unit-aware model ``__repr__`` output.

    Returns
    -------
    UnitSystem
        The strategy active after setting it.
    """
    global current_unit_system
    current_unit_system = unit_system
    return current_unit_system


@contextmanager
def unit_system(mode: UnitSystem) -> Iterator[None]:
    """Temporarily select a unit representation strategy.

    Parameters
    ----------
    mode : UnitSystem
        Strategy to apply to unit-aware model ``__repr__`` output in the context.

    Yields
    ------
    None

    Examples
    --------
    >>> previous = get_unit_system()
    >>> with unit_system(UnitSystem.NATURAL_UNITS):
    ...     assert get_unit_system() is UnitSystem.NATURAL_UNITS
    >>> assert get_unit_system() is previous
    """
    previous = get_unit_system()
    set_unit_system(mode)
    try:
        yield
    finally:
        set_unit_system(previous)


__all__ = [
    "HasPerUnit",
    "HasUnits",
    "Unit",
    "UnitSpec",
    "UnitSystem",
    "UnitInput",
    "convert_to_internal",
    "format_for_display",
    "get_unit_system",
    "set_unit_system",
    "unit_spec",
    "unit_system",
    "ureg",
]
