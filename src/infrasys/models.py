"""Base models for the package"""

import abc
from typing import Any
from uuid import UUID, uuid4

from loguru import logger
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema


def make_model_config(**kwargs: Any) -> ConfigDict:
    """Return a Pydantic config"""
    return ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        validate_default=True,
        extra="forbid",
        use_enum_values=False,
        arbitrary_types_allowed=True,
        populate_by_name=True,
        **kwargs,  # type: ignore
    )


class InfraSysBaseModel(BaseModel):
    """Base class for all Infrastructure Systems models"""

    model_config = make_model_config()


class InfraSysBaseModelWithIdentifiers(InfraSysBaseModel, abc.ABC):
    """Base class for Infrastructure Systems types with stable integer identifiers."""

    id: int | None = Field(
        default=None,
        ge=1,
        repr=False,
        validation_alias=AliasChoices("id"),
    )
    legacy_uuid: SkipJsonSchema[UUID] = Field(
        default_factory=uuid4,
        exclude=True,
        repr=False,
        validation_alias=AliasChoices("legacy_uuid", "uuid"),
    )

    @property
    def uuid(self) -> UUID:
        """Return the legacy UUID for backwards-compatible API access."""
        return self.legacy_uuid

    @uuid.setter
    def uuid(self, value: UUID) -> None:
        self.legacy_uuid = value

    def assign_new_uuid(self):
        """Generate a new legacy UUID."""
        self.legacy_uuid = uuid4()
        logger.debug("Assigned new legacy UUID for {}: {}", self.label, self.legacy_uuid)

    @classmethod
    def example(cls) -> "InfraSysBaseModelWithIdentifers":
        """Return an example instance of the model.

        Raises
        ------
        NotImplementedError
            Raised if the model does not implement this method.
        """
        msg = f"{cls.__name__} does not implement example()"
        raise NotImplementedError(msg)

    @property
    def label(self) -> str:
        """Provides a description of an instance."""
        class_name = self.__class__.__name__
        name = getattr(self, "name", "") or str(self.id or self.legacy_uuid)
        return make_label(class_name, name)


InfraSysBaseModelWithIdentifers = InfraSysBaseModelWithIdentifiers


def make_label(class_name: str, name: str) -> str:
    """Make a string label of an instance."""
    return f"{class_name}.{name}"


def get_class_and_name_from_label(label: str) -> tuple[str, str | int | UUID]:
    """Return the class and name from a label.
    If the name is a stringified UUID, it will be converted to a UUID.
    """
    class_name, name = label.split(".", maxsplit=1)
    name_or_uuid: str | int | UUID = name
    try:
        name_or_uuid = int(name)
        return class_name, name_or_uuid
    except ValueError:
        pass
    try:
        name_or_uuid = UUID(name)
    except ValueError:
        pass

    return class_name, name_or_uuid
