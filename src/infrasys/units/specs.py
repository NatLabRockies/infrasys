"""Unit specification and annotation types."""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union, cast, get_args, get_origin

from pydantic import BaseModel, GetCoreSchemaHandler
from pydantic_core import core_schema

from .conversion import convert_unit_input
from .schema import (
    PER_UNIT_UNIT,
    STRUCTURED_UNIT_SCHEMA_METADATA_KEY,
    StructuredPerUnitMapping,
    UNIT_KEY,
    UNIT_SPEC_METADATA_KEY,
    VALUE_KEY,
)
from .validation import reject_boolean_magnitude, validate_structured_base

if TYPE_CHECKING:
    from pydantic import GetJsonSchemaHandler
    from pydantic.json_schema import JsonSchemaValue


def unit_value_type(annotation: object) -> object:
    """Return the value type underlying an optional unit field annotation.

    For ``T | None``, this returns ``T`` so schema construction can inspect the
    annotated value type without treating ``None`` as the structured type.
    Other annotations are returned unchanged.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        return next(
            (argument for argument in get_args(annotation) if argument is not type(None)),
            annotation,
        )
    return annotation


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """Immutable metadata descriptor for a unit-aware field.

    A ``UnitSpec`` can be created independently of any model. Its ``base`` is a
    field-name reference, not a fixed value. When a ``HasUnits`` model uses the
    specification, that model resolves the referenced field's unit and supplies
    its value during conversion. The same annotation can therefore be reused by
    models with different base values.

    Attributes
    ----------
    unit : str
        Unit string, such as ``"MVA"``, ``"pu"``, or ``"kV"``.
    base : str or None
        Name of the model field that supplies the device base.
    """

    unit: str
    base: str | None = field(default=None, kw_only=True)

    def convert_structured_value(
        self,
        value: BaseModel,
        *,
        info: core_schema.ValidationInfo,
    ) -> BaseModel:
        """Convert a structured natural-unit value to internal per-unit form.

        The base comes from the already-validated outer model fields in
        ``info.data``. If this spec has no base, the structured value is returned
        unchanged. For nested ``HasUnits`` models, only unit fields without their
        own base are divided by the outer base. For other Pydantic models, numeric
        fields are divided. Booleans and nonnumeric fields remain unchanged.

        Parameters
        ----------
        value : BaseModel
            Parsed nested value supplied by Pydantic.
        info : ValidationInfo
            Validation context containing the outer model's earlier fields.

        Returns
        -------
        BaseModel
            The nested value with eligible numeric fields converted.

        Raises
        ------
        ValueError
            If the required outer base is missing, zero, or non-finite.
        """
        if self.base is None:
            return value

        base_value = validate_structured_base(
            info.data.get(self.base) if info.data is not None else None,
            base_field=self.base,
        )

        from .mixins import HasUnits

        model_fields = type(value).model_fields
        # Excluded fields remain validation inputs; normalized nested values prevent reconversion.
        structured_values: dict[str, object] = {
            field_name: getattr(value, field_name) for field_name in model_fields
        }
        structured_values.update(value.model_dump(round_trip=True, by_alias=False))
        converted_fields: dict[str, object] = {}

        if isinstance(value, HasUnits):
            convertible_fields = {
                field_name
                for field_name, field_spec in type(value).unit_specs.items()
                if field_spec.base is None
            }
            for field_name, field_value in structured_values.items():
                if (
                    field_name in convertible_fields
                    and isinstance(field_value, (int, float))
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
                and issubclass(field_type, (int, float))
                and not issubclass(field_type, bool)
            )

            if (
                is_numeric
                and isinstance(field_value, (int, float))
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
        """Build the Pydantic schema for a unit-annotated field.

        Scalar fields accept an internal number or a unit mapping with
        ``"value"`` and ``"unit"`` fields, normalized to the declared unit.
        Structured fields use Pydantic's model schema and convert natural values
        to internal per-unit values when a base is declared. Relative structured
        fields serialize as ``{"value": ..., "unit": "pu"}`` to mark the value
        as already normalized and prevent applying the base a second time.

        Parameters
        ----------
        source_type : Any
            The field's annotated value type supplied by Pydantic.
        handler : GetCoreSchemaHandler
            Pydantic's handler for constructing the underlying field schema.

        Returns
        -------
        CoreSchema
            The combined validation and serialization schema for the field.
        """
        actual_type = unit_value_type(source_type)

        is_structured_type = isinstance(actual_type, type) and issubclass(actual_type, BaseModel)

        if is_structured_type:
            inner_schema = handler(source_type)

            def validate_structured(
                value: object,
                next_validator: core_schema.ValidatorFunctionWrapHandler,
                info: core_schema.ValidationInfo,
            ) -> object:
                """Validate natural values or read the standard per-unit mapping."""
                if (
                    self.base is not None
                    and isinstance(value, dict)
                    and {VALUE_KEY, UNIT_KEY} <= value.keys()
                    and isinstance(value[VALUE_KEY], dict)
                ):
                    if set(value) != {VALUE_KEY, UNIT_KEY} or value[UNIT_KEY] != PER_UNIT_UNIT:
                        msg = (
                            f"Serialized structured unit value must be "
                            f"{{{VALUE_KEY!r}: ..., {UNIT_KEY!r}: {PER_UNIT_UNIT!r}}}"
                        )
                        raise ValueError(msg)
                    return next_validator(value[VALUE_KEY])
                parsed: BaseModel | None = next_validator(value)
                return None if parsed is None else self.convert_structured_value(parsed, info=info)

            serialized_schema = inner_schema
            if self.base is not None:
                serialized_schema = core_schema.typed_dict_schema(
                    {
                        VALUE_KEY: core_schema.typed_dict_field(inner_schema),
                        UNIT_KEY: core_schema.typed_dict_field(
                            core_schema.literal_schema([PER_UNIT_UNIT])
                        ),
                    },
                    extra_behavior="forbid",
                )
                if type(None) in get_args(source_type):
                    serialized_schema = core_schema.nullable_schema(serialized_schema)

            def serialize_structured(
                value: object, next_serializer: core_schema.SerializerFunctionWrapHandler
            ) -> object:
                """Keep Pydantic's model serialization and mark relative values."""
                result: dict[str, object] = next_serializer(value)
                return (
                    cast(
                        StructuredPerUnitMapping,
                        {VALUE_KEY: result, UNIT_KEY: PER_UNIT_UNIT},
                    )
                    if self.base is not None
                    else result
                )

            return core_schema.with_info_wrap_validator_function(
                validate_structured,
                inner_schema,
                json_schema_input_schema=core_schema.union_schema(
                    [inner_schema, serialized_schema]
                ),
                metadata={STRUCTURED_UNIT_SCHEMA_METADATA_KEY: serialized_schema},
                serialization=core_schema.wrap_serializer_function_ser_schema(
                    serialize_structured,
                    schema=inner_schema,
                    return_schema=core_schema.dict_schema(),
                    when_used="unless-none",
                ),
            )

        def validate_scalar(
            value: object,
            next_validator: core_schema.ValidatorFunctionWrapHandler,
            info: core_schema.ValidationInfo,
            *,
            base_unit: str | None = None,
        ) -> object:
            """Normalize unit mappings before Pydantic validates scalar values."""
            reject_boolean_magnitude(value)
            if isinstance(value, dict) and {VALUE_KEY, UNIT_KEY} <= value.keys():
                base_value: object | None = (
                    info.data.get(self.base) if self.base is not None and info.data else None
                )
                value = convert_unit_input(
                    value, spec=self, base_value=base_value, base_unit=base_unit
                )
            return next_validator(value)

        inner_schema = handler(source_type)
        unit_mapping_schema = core_schema.typed_dict_schema(
            {
                VALUE_KEY: core_schema.typed_dict_field(core_schema.float_schema()),
                UNIT_KEY: core_schema.typed_dict_field(core_schema.str_schema(min_length=1)),
            },
            extra_behavior="allow",
        )
        return core_schema.with_info_wrap_validator_function(
            validate_scalar,
            inner_schema,
            json_schema_input_schema=core_schema.union_schema([inner_schema, unit_mapping_schema]),
            metadata={UNIT_SPEC_METADATA_KEY: self},
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        """Return validation and serialization JSON schemas for a unit field.

        Validation schemas describe accepted unit mappings. Serialization
        schemas for relative structured values describe the standard per-unit
        mapping used by ``__get_pydantic_core_schema__``.

        Parameters
        ----------
        schema : CoreSchema
            The core schema whose JSON schema is being requested.
        handler : GetJsonSchemaHandler
            Pydantic's handler for generating the default JSON schema.

        Returns
        -------
        JsonSchemaValue
            The validation or serialization JSON schema.
        """
        structured_schema = schema.get("metadata", {}).get(STRUCTURED_UNIT_SCHEMA_METADATA_KEY)
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
        Name of the model field that supplies the device base. The owning
        ``HasUnits`` model resolves the field and its value.

    Returns
    -------
    UnitSpec
        Unit metadata for use with ``typing.Annotated``.
    """
    return UnitSpec(unit=unit, base=base)


Unit = unit_spec
