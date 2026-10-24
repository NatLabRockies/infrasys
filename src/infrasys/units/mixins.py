"""Mixins for unit-aware Pydantic models."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from .specs import UnitSpec

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

__all__ = ["HasPerUnit", "HasUnits"]


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

        return _bind_unit_nodes(schema, cls)

    @property
    def system_base(self) -> float | None:
        """Return the system base when this model supports it."""
        return None

    def __repr_args__(self) -> list[tuple[str | None, Any]]:
        """Format model fields using the active unit display mode.

        Returns
        -------
        list[tuple[str | None, Any]]
            Field names and display-formatted values used by ``repr``.
        """
        from . import get_unit_system
        from .conversion import format_for_display

        repr_args: list[tuple[str | None, Any]] = []
        unit_system = get_unit_system()

        for field_name, field_info in type(self).model_fields.items():
            if field_name.startswith("_") or not field_info.repr:
                continue

            value = getattr(self, field_name)
            spec = type(self).unit_specs.get(field_name)
            if spec is None or not isinstance(value, int | float):
                repr_args.append((field_name, value))
                continue

            base_value = getattr(self, spec.base) if spec.base is not None else None
            base_unit = type(self).unit_specs[spec.base].unit if spec.base is not None else None
            formatted = format_for_display(
                value,
                spec,
                unit_system,
                base_value,
                base_unit,
                self.system_base,
            )
            repr_args.append((field_name, formatted))

        return repr_args


class HasPerUnit(HasUnits):
    """Add system-base display state to a unit-aware model."""

    @property
    def system_base(self) -> float | None:
        """Return the system base power bound to this model."""
        return self.__dict__.get("system_base")

    def bind_system_base(self, base_power: float | None) -> None:
        """Set the system base used for per-unit display.

        Parameters
        ----------
        base_power : float or None
            System base power to bind to this model.
        """
        self.__dict__["system_base"] = base_power


def _bind_unit_nodes(
    schema: core_schema.CoreSchema, model_type: type[HasUnits]
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
                name: {**field, "schema": _bind_unit_nodes(field["schema"], model_type)}
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
        return {**schema, "schema": _bind_unit_nodes(schema["schema"], model_type)}
    return schema
