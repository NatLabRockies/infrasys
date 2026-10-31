"""Mixins for unit-aware Pydantic models."""

from __future__ import annotations

from functools import partial
from reprlib import recursive_repr
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, GetCoreSchemaHandler, PrivateAttr
from pydantic_core import core_schema

from .specs import UnitSpec

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


def bind_unit_nodes(
    schema: core_schema.CoreSchema, *, model_type: type[HasUnits]
) -> core_schema.CoreSchema:
    """Bind only validation schema edges, never defaults or user metadata."""
    if schema["type"] == "model" and schema["cls"] is not model_type:
        return schema

    spec = schema.get("metadata", {}).get("infrasys.unit_spec")
    if schema["type"] == "function-wrap" and isinstance(spec, UnitSpec) and spec.base is not None:
        function = schema["function"]
        return {
            **schema,
            "function": {
                **function,
                "function": partial(
                    function["function"], base_unit=model_type.unit_specs[spec.base].unit
                ),
            },
        }

    if schema["type"] == "model-fields":
        return {
            **schema,
            "fields": {
                name: {**field, "schema": bind_unit_nodes(field["schema"], model_type=model_type)}
                for name, field in schema["fields"].items()
            },
        }
    if schema["type"] in (
        "definitions",
        "model",
        "function-before",
        "function-after",
        "function-wrap",
        "default",
        "nullable",
    ):
        return {**schema, "schema": bind_unit_nodes(schema["schema"], model_type=model_type)}
    return schema


class HasUnits:
    """Add unit metadata and display formatting to a Pydantic model.

    Unit specifications are collected whenever Pydantic builds a model schema.
    Base units are resolved from the owning model without mutating annotations.
    """

    unit_specs: ClassVar[dict[str, UnitSpec]] = {}

    if TYPE_CHECKING:
        model_fields: ClassVar[dict[str, FieldInfo]]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Collect and validate unit metadata after field annotations resolve."""
        schema = handler(source_type)

        specs: dict[str, UnitSpec] = {}
        for field_name, field_info in cls.model_fields.items():
            for metadata in field_info.metadata:
                if isinstance(metadata, UnitSpec):
                    specs[field_name] = metadata

        for field_name, spec in specs.items():
            if spec.base is None:
                continue
            if spec.base not in specs:
                msg = f"{cls.__name__}.{field_name} references base field {spec.base!r}, which has no UnitSpec"
                raise TypeError(msg)

        cls.unit_specs = specs

        return bind_unit_nodes(schema, model_type=cls)

    @property
    def system_base(self) -> float | None:
        """Return the system base when this model supports it."""
        return None

    @recursive_repr()
    def __repr__(self) -> str:
        """Format model fields using the active unit display mode."""
        from . import get_unit_system
        from .conversion import format_for_display

        unit_system = get_unit_system()
        fields: list[str] = []

        for field_name, field_info in type(self).model_fields.items():
            if field_name.startswith("_") or not field_info.repr:
                continue

            value = getattr(self, field_name)
            spec = type(self).unit_specs.get(field_name)
            if spec is not None and isinstance(value, int | float):
                base_value = getattr(self, spec.base) if spec.base is not None else None
                base_unit = (
                    type(self).unit_specs[spec.base].unit if spec.base is not None else None
                )
                value = format_for_display(
                    value,
                    spec=spec,
                    unit_system=unit_system,
                    base_value=base_value,
                    base_unit=base_unit,
                    system_base=self.system_base,
                )
            fields.append(f"{field_name}={value!r}")

        return f"{type(self).__name__}({', '.join(fields)})"


class HasPerUnit(HasUnits, BaseModel):
    """Add system-base display state to a unit-aware model."""

    _system_base: float | None = PrivateAttr(default=None)

    @property
    def system_base(self) -> float | None:
        """Return the system base power bound to this model."""
        return self._system_base

    def bind_system_base(self, base_power: float | None) -> None:
        """Set the system base used for per-unit display.

        Parameters
        ----------
        base_power : float or None
            System base power to bind to this model.
        """
        self._system_base = base_power
