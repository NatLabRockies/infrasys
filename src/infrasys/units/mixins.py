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

        def bind_unit_nodes(node: Any) -> Any:
            if isinstance(node, dict):
                if node.get("type") == "definitions":
                    return {**node, "schema": bind_unit_nodes(node["schema"])}
                # Nested models bind their own units, even when they share field names.
                if node.get("type") == "model" and node["cls"] is not cls:
                    return node
                metadata = node.get("metadata")
                spec = metadata.get("infrasys.unit_spec") if isinstance(metadata, dict) else None
                if isinstance(spec, UnitSpec):
                    if spec.base is None:
                        return node
                    return {
                        **node,
                        "function": {
                            **node["function"],
                            "function": partial(
                                node["function"]["function"], base_unit=specs[spec.base].unit
                            ),
                        },
                    }
                return {key: bind_unit_nodes(value) for key, value in node.items()}
            if isinstance(node, list):
                return [bind_unit_nodes(value) for value in node]
            return node

        return bind_unit_nodes(schema)

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

        for field_name in type(self).model_fields:
            if field_name.startswith("_"):
                continue

            value = getattr(self, field_name)
            spec = type(self).unit_specs.get(field_name)
            if spec is None or value is None:
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
