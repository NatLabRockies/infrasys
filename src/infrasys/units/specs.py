"""Unit specification and annotation types."""

from __future__ import annotations

import math
import types
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

from pydantic import BaseModel, GetCoreSchemaHandler
from pydantic_core import core_schema

from .conversion import convert_to_internal

if TYPE_CHECKING:
    from pydantic import GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue

__all__ = ["Unit", "UnitSpec", "unit_spec"]


def unit_value_type(annotation: Any) -> Any:
    """Return the non-None value type of a unit field annotation."""
    if get_origin(annotation) in (Union, types.UnionType):
        return next(
            (argument for argument in get_args(annotation) if argument is not type(None)),
            annotation,
        )
    return annotation


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """Immutable metadata descriptor for unit-aware fields.

    The owning model resolves base units from its own fields. Annotations can
    therefore be shared across models without retaining owner-specific state.

    Attributes
    ----------
    unit : str
        Unit string, such as ``"MVA"``, ``"pu"``, or ``"kV"``.
    base : str or None
        Field name for device-base lookup.
    """

    unit: str
    base: str | None = None

    def convert_structured_value(
        self,
        value: BaseModel,
        info: core_schema.ValidationInfo,
    ) -> BaseModel:
        """Convert numeric fields of a structured value to per-unit form."""
        if self.base is None:
            return value

        base_value = info.data.get(self.base) if info.data is not None else None
        if base_value is None:
            msg = f"Base value {self.base!r} is required for unit conversion"
            raise ValueError(msg)
        if not math.isfinite(base_value) or base_value == 0:
            msg = f"Base value {self.base!r} must be finite and non-zero for unit conversion"
            raise ValueError(msg)

        from .mixins import HasUnits

        model_fields = type(value).model_fields
        # Excluded fields remain validation inputs; nested envelopes prevent reconversion.
        structured_values = {
            field_name: getattr(value, field_name) for field_name in model_fields
        } | value.model_dump(round_trip=True, by_alias=False)
        converted_fields: dict[str, Any] = {}

        if isinstance(value, HasUnits):
            convertible_fields = {
                field_name
                for field_name, field_spec in type(value).unit_specs.items()
                if field_spec.base is None
            }
            for field_name, field_value in structured_values.items():
                if (
                    field_name in convertible_fields
                    and isinstance(field_value, int | float)
                    and not isinstance(field_value, bool)
                ):
                    converted_fields[field_name] = field_value / base_value
                else:
                    converted_fields[field_name] = field_value
            return type(value).model_validate(converted_fields, by_name=True)

        for field_name, field_value in structured_values.items():
            field_info = model_fields.get(field_name)
            if field_info is None:
                converted_fields[field_name] = field_value
                continue

            field_type = unit_value_type(field_info.annotation)

            is_numeric = field_type in (int, float) or (
                isinstance(field_type, type)
                and issubclass(field_type, int | float)
                and not issubclass(field_type, bool)
            )

            if (
                is_numeric
                and isinstance(field_value, int | float)
                and not isinstance(field_value, bool)
            ):
                converted_fields[field_name] = field_value / base_value
            else:
                converted_fields[field_name] = field_value

        return type(value).model_validate(converted_fields, by_name=True)

    def __get_pydantic_core_schema__(
        self,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        """Build validation and serialization behavior for an annotated field.

        Structured per-unit fields serialize as ``{"value": ..., "unit": "pu"}``.
        This explicit envelope marks stored internal values and prevents a second
        natural-unit conversion when validating serialized data.
        """
        actual_type = unit_value_type(source_type)

        is_structured_type = isinstance(actual_type, type) and issubclass(actual_type, BaseModel)

        if is_structured_type:
            inner_schema = handler(source_type)

            def validate_structured(
                value: Any,
                next_validator: core_schema.ValidatorFunctionWrapHandler,
                info: core_schema.ValidationInfo,
            ) -> Any:
                """Accept natural structured values or the explicit internal envelope."""
                if (
                    self.base is not None
                    and isinstance(value, dict)
                    and {"value", "unit"} <= value.keys()
                    and isinstance(value["value"], dict)
                ):
                    if set(value) != {"value", "unit"} or value["unit"] != "pu":
                        msg = (
                            "Serialized structured unit value must be {'value': ..., 'unit': 'pu'}"
                        )
                        raise ValueError(msg)
                    return next_validator(value["value"])
                parsed = next_validator(value)
                return None if parsed is None else self.convert_structured_value(parsed, info)

            serialized_schema = inner_schema
            if self.base is not None:
                serialized_schema = core_schema.typed_dict_schema(
                    {
                        "value": core_schema.typed_dict_field(inner_schema),
                        "unit": core_schema.typed_dict_field(core_schema.literal_schema(["pu"])),
                    },
                    extra_behavior="forbid",
                )
                if type(None) in get_args(source_type):
                    serialized_schema = core_schema.nullable_schema(serialized_schema)

            def serialize_structured(
                value: Any, next_serializer: core_schema.SerializerFunctionWrapHandler
            ) -> Any:
                """Serialize internal values while honoring Pydantic serialization options."""
                result = next_serializer(value)
                return {"value": result, "unit": "pu"} if self.base is not None else result

            return core_schema.with_info_wrap_validator_function(
                validate_structured,
                inner_schema,
                json_schema_input_schema=core_schema.union_schema(
                    [inner_schema, serialized_schema]
                ),
                metadata={"infrasys.structured_unit_schema": serialized_schema},
                serialization=core_schema.wrap_serializer_function_ser_schema(
                    serialize_structured,
                    schema=inner_schema,
                    return_schema=core_schema.dict_schema(),
                    when_used="unless-none",
                ),
            )

        def validate_scalar(
            value: Any,
            next_validator: core_schema.ValidatorFunctionWrapHandler,
            info: core_schema.ValidationInfo,
            *,
            base_unit: str | None = None,
        ) -> Any:
            if isinstance(value, dict):
                base_value = (
                    info.data.get(self.base) if self.base is not None and info.data else None
                )
                value = convert_to_internal(
                    value, self, base_value=base_value, base_unit=base_unit
                )
            return next_validator(value)

        inner_schema = handler(source_type)
        envelope_schema = core_schema.typed_dict_schema(
            {
                "value": core_schema.typed_dict_field(core_schema.float_schema()),
                "unit": core_schema.typed_dict_field(core_schema.str_schema(min_length=1)),
            },
            extra_behavior="allow",
        )
        return core_schema.with_info_wrap_validator_function(
            validate_scalar,
            inner_schema,
            json_schema_input_schema=core_schema.union_schema([inner_schema, envelope_schema]),
            metadata={"infrasys.unit_spec": self},
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Describe the field's scalar or structured unit representation."""
        structured_schema = schema.get("metadata", {}).get("infrasys.structured_unit_schema")
        if handler.mode == "serialization" and structured_schema is not None:
            return handler(structured_schema)
        return handler(schema)


def unit_spec(
    unit: str,
    *,
    base: str | None = None,
) -> UnitSpec:
    """Create a unit specification for an annotated model field.

    Parameters
    ----------
    unit : str
        Unit string, such as ``"MVA"``, ``"kV"``, or ``"pu"``.
    base : str or None, optional
        Name of the field that provides the device base.

    Returns
    -------
    UnitSpec
        Unit metadata for use with ``typing.Annotated``.
    """
    return UnitSpec(unit=unit, base=base)


Unit = unit_spec
