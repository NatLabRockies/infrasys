"""Defines base models for components."""

from typing import Any

import pint
from pydantic import Field
from rich import print as _pprint
from typing_extensions import Annotated

from infrasys.models import (
    InfraSysBaseModelWithIdentifers,
)
from infrasys.quantities import BaseQuantity
from infrasys.serialization import (
    TYPE_METADATA,
    SerializedComponentReference,
    SerializedQuantityType,
    SerializedTypeMetadata,
    serialize_value,
)


class Component(InfraSysBaseModelWithIdentifers):
    """Base class for all models representing entities that get attached to a System."""

    name: Annotated[str, Field(frozen=True)]

    def check_component_addition(self) -> None:
        """Perform checks on the component before adding it to a system."""

    def model_dump_custom(self, *args, **kwargs) -> dict[str, Any]:
        """Custom serialization for this package"""
        refs = {}
        for x in type(self).model_fields:
            val = self._model_dump_field(x)
            if val is not None:
                refs[x] = val
        exclude = kwargs.get("exclude", [])
        exclude += list(set(exclude).union(refs))
        kwargs["exclude"] = exclude
        data = serialize_value(self, *args, **kwargs)
        data.update(refs)
        return data

    def _model_dump_field(self, field) -> Any:
        val = getattr(self, field)
        if isinstance(val, Component):
            val = {TYPE_METADATA: serialize_component_reference(val)}
        elif isinstance(val, list) and val and isinstance(val[0], Component):
            val = [{TYPE_METADATA: serialize_component_reference(x)} for x in val]
        elif isinstance(val, BaseQuantity | pint.Quantity):
            data = val.to_dict()
            data[TYPE_METADATA] = SerializedTypeMetadata.validate_python(
                SerializedQuantityType(
                    module=val.__module__,
                    type=val.__class__.__name__,
                ),
            ).model_dump()
            val = data
        else:
            val = None
        # TODO: other composite types may need handling.
        # Parent packages can always implement a field_serializer themselves.
        return val

    def pprint(self):
        return _pprint(self)


def serialize_component_reference(component: Component) -> dict[str, Any]:
    """Make a JSON serializable reference to a component."""
    if component.id is None:
        msg = f"Component {component.label} must be attached before it can be serialized"
        raise ValueError(msg)
    return SerializedTypeMetadata.validate_python(
        SerializedComponentReference(
            module=component.__module__,
            type=component.__class__.__name__,
            id=component.id,
        ),
    ).model_dump(by_alias=True)
