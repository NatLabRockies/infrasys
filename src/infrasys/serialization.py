import enum
import importlib
from typing import Annotated, Any, Literal, Type, TypeAlias, Union
from uuid import UUID

from pydantic import AliasChoices, Field, TypeAdapter
from pydantic.json_schema import SkipJsonSchema

from infrasys.models import InfraSysBaseModel
from infrasys.time_series_models import TimeSeriesData

TYPE_METADATA = "__metadata__"
SERIALIZED_FIELDS = {"quantity_metadata", "normalization"}


class SerializedType(str, enum.Enum):
    """Controls how types are serialized."""

    BASE = "base"
    COMPOSED_COMPONENT = "composed_component"
    QUANTITY = "quantity"


class SerializedTypeBase(InfraSysBaseModel):
    """Applies to all normal types"""

    module: str
    type: str
    model_config = {"extra": "ignore"}


class SerializedBaseType(SerializedTypeBase):
    """Applies to all normal types"""

    serialized_type: Literal[SerializedType.BASE] = SerializedType.BASE


class SerializedComponentReference(SerializedTypeBase):
    """Reference information for a component serialized inside another component.

    Integer IDs are the primary key. Legacy UUIDs are preserved for backward
    compatibility during deserialization but are excluded from serialized output.
    """

    serialized_type: Literal[SerializedType.COMPOSED_COMPONENT] = (
        SerializedType.COMPOSED_COMPONENT
    )
    id: int = Field(
        ge=1,
        validation_alias=AliasChoices("id"),
        description="Integer ID referencing the serialized component",
    )
    legacy_uuid: SkipJsonSchema[UUID | None] = Field(
        default=None,
        exclude=True,
        validation_alias=AliasChoices("legacy_uuid", "uuid"),
        description=(
            "Legacy UUID reference accepted during deserialization for backward "
            "compatibility; never included in serialized output"
        ),
    )

    @property
    def uuid(self) -> UUID:
        """Return the legacy referenced component UUID for backwards-compatible callers."""
        if self.legacy_uuid is None:
            msg = "Serialized component reference does not contain a legacy UUID"
            raise AttributeError(msg)
        return self.legacy_uuid


class SerializedQuantityType(SerializedTypeBase):
    serialized_type: Literal[SerializedType.QUANTITY] = SerializedType.QUANTITY


MetadataType: TypeAlias = Annotated[
    Union[
        SerializedBaseType,
        SerializedComponentReference,
        SerializedQuantityType,
    ],
    Field(discriminator="serialized_type"),
]
SerializedTypeMetadata: TypeAdapter[MetadataType] = TypeAdapter(
    Annotated[
        Union[
            SerializedBaseType,
            SerializedComponentReference,
            SerializedQuantityType,
        ],
        Field(discriminator="serialized_type"),
    ]
)


class CachedTypeHelper:
    """Helper class to deserialize types."""

    def __init__(self) -> None:
        self._observed_types: dict[tuple[str, str], Type] = {}
        self._deserialized_types: set[Type] = set()

    def add_deserialized_type(self, deserialized_type: Type[Any]) -> None:
        """Add type that has been deserialized."""
        self._deserialized_types.add(deserialized_type)

    def add_deserialized_types(self, types: set[Type]) -> None:
        """Add types that have been deserialized."""
        self._deserialized_types.update(types)

    def allowed_to_deserialize(self, component_type: Type) -> bool:
        """Return True if the type can be deserialized."""
        return component_type in self._deserialized_types

    def get_type(self, metadata: SerializedTypeBase) -> Type:
        """Return the type contained in metadata, dynamically importing as necessary."""
        type_key = (metadata.module, metadata.type)
        component_type = self._observed_types.get(type_key)
        if component_type is None:
            component_type = _deserialize_type(*type_key)
            self._observed_types[type_key] = component_type
        return component_type


def serialize_value(obj: InfraSysBaseModel, *args, **kwargs) -> dict[str, Any]:
    """Serialize an infrasys object to a dictionary."""
    cls = type(obj)
    data = obj.model_dump(*args, mode="json", round_trip=True, **kwargs)
    data[TYPE_METADATA] = SerializedTypeMetadata.validate_python(
        SerializedBaseType(
            module=cls.__module__,
            type=cls.__name__,
        ),
    ).model_dump()
    return data


def deserialize_type(metadata: SerializedTypeBase) -> Type["TimeSeriesData"]:
    """Dynamically import the type and return it."""
    return _deserialize_type(metadata.module, metadata.type)


def _deserialize_type(module, obj_type) -> Type:
    mod = importlib.import_module(module)
    return getattr(mod, obj_type)


def deserialize_value(data: dict[str, Any], metadata: SerializedTypeBase) -> Any:
    """Deserialize the value from a dictionary."""
    ctype = deserialize_type(metadata)
    # We ignore any additional data.
    return ctype.model_validate(
        {key: value for key, value in data.items() if key in ctype.model_fields}
    )
