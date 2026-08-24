"""Base models for the package"""

import abc
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
        name = getattr(self, "name", "") or str(self.id)
        return make_label(class_name, name)


InfraSysBaseModelWithIdentifers = InfraSysBaseModelWithIdentifiers


def make_label(class_name: str, name: str) -> str:
    """Make a string label of an instance."""
    return f"{class_name}.{name}"


def get_class_and_name_from_label(label: str) -> tuple[str, str]:
    """Return the class and name from a label.

    Numeric names are returned as strings so that callers can attempt a name-based
    lookup first and fall back to an ID-based lookup only when the name is not found.
    """
    class_name, name = label.split(".", maxsplit=1)
    return class_name, name
