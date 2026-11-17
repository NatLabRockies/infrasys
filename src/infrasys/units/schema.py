"""Shared tokens and types for unit envelopes and Pydantic schema metadata."""

from __future__ import annotations

from typing import Final, Generic, Literal, TypeAlias, TypeVar, TypedDict

VALUE_KEY: Final = "value"
UNIT_KEY: Final = "unit"
PerUnitLabel: TypeAlias = Literal["pu"]
PER_UNIT_UNIT: Final[PerUnitLabel] = "pu"
UNIT_SPEC_METADATA_KEY: Final = "infrasys.unit_spec"
STRUCTURED_UNIT_SCHEMA_METADATA_KEY: Final = "infrasys.structured_unit_schema"


ValueType = TypeVar("ValueType")
UnitType = TypeVar("UnitType", bound=str)


class UnitInput(TypedDict, Generic[ValueType, UnitType]):
    """A unit envelope whose payload and unit can be specialized by context."""

    value: ValueType
    unit: UnitType


StructuredPerUnitEnvelope: TypeAlias = UnitInput[dict[str, object], PerUnitLabel]


def make_structured_per_unit_envelope(
    value: dict[str, object],
) -> StructuredPerUnitEnvelope:
    """Wrap an already-internal structured value in its serialization envelope."""
    return {VALUE_KEY: value, UNIT_KEY: PER_UNIT_UNIT}
