"""Unit handling for power system models.

This module provides unit-aware field annotations, automatic conversion between
natural units and per-unit values, and configurable display formatting.

Notes
-----
* Annotate numeric fields with ``Annotated[float, Unit("kV"|"MVA"|"pu", base="base_field")]``
* Natural-unit inputs (``{"value": 138, "unit": "kV"}``) are converted to per-unit when ``base`` is set
* Internal storage is always float (device-base per-unit for relative quantities)
* Global display mode (device base, system base, or natural units) affects ``__repr__`` formatting only
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum

from .conversion import ureg
from .mixins import HasPerUnit, HasUnits
from .specs import Unit, UnitSpec, unit_spec


class UnitSystem(str, Enum):
    """Display modes for formatted representation.

    Attributes
    ----------
    DEVICE_BASE : str
        Display per-unit values relative to device base
    SYSTEM_BASE : str
        Display per-unit values relative to system base
    NATURAL_UNITS : str
        Display values in their natural units (e.g., kV, MVA)
    """

    DEVICE_BASE = "DEVICE_BASE"
    SYSTEM_BASE = "SYSTEM_BASE"
    NATURAL_UNITS = "NATURAL_UNITS"


current_unit_system: UnitSystem = UnitSystem.DEVICE_BASE


def get_unit_system() -> UnitSystem:
    """Get the current global display mode.

    Returns
    -------
    UnitSystem
        Current display mode
    """
    return current_unit_system


def set_unit_system(unit_system: UnitSystem) -> None:
    """Set the global display mode.

    Parameters
    ----------
    unit_system : UnitSystem
        Display mode to set
    """
    global current_unit_system
    current_unit_system = unit_system


@contextmanager
def unit_system(mode: UnitSystem) -> Iterator[None]:
    """Context manager for temporary display mode changes.

    Parameters
    ----------
    mode : UnitSystem
        Temporary display mode to use within the context

    Yields
    ------
    None

    Examples
    --------
    >>> gen = Generator(...)
    >>> with unit_system(UnitSystem.NATURAL_UNITS):
    ...     print(gen)  # Displays in natural units
    >>> print(gen)  # Back to previous mode
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
    "get_unit_system",
    "set_unit_system",
    "unit_spec",
    "unit_system",
    "ureg",
]
