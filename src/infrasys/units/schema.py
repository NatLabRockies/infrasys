"""Standard unit mappings and Pydantic metadata identifiers.

A unit mapping keeps a magnitude and its unit together as
``{"value": ..., "unit": ...}``. Structured values normalized against a model
base serialize with ``"unit": "pu"``. That marker distinguishes already-normalized
values from natural-unit input and prevents applying the base twice.

``pu`` alone does not identify a reference quantity. The model using a unit
specification resolves its ``base`` field and supplies that field's value during
conversion.
"""

from __future__ import annotations

from typing import Final, Generic, Literal, TypeVar, TypedDict

VALUE_KEY: Final = "value"
UNIT_KEY: Final = "unit"
PER_UNIT_UNIT: Final[Literal["pu"]] = "pu"
UNIT_SPEC_METADATA_KEY: Final = "infrasys.unit_spec"
UNIT_SCHEMA_METADATA_KEY: Final = "infrasys.unit_schema"


ValueType = TypeVar("ValueType")
UnitType = TypeVar("UnitType", bound=str)


class UnitInput(TypedDict, Generic[ValueType, UnitType]):
    """A unit mapping with separately typed magnitude and unit fields."""

    value: ValueType
    unit: UnitType
