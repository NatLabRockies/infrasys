"""Manages components"""

import copy
import itertools
from collections import defaultdict
from typing import Any, Callable, Iterable, Optional, Type
from uuid import UUID
from loguru import logger

from infrasys.component import Component
from infrasys.component_associations import ComponentAssociations
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.id_manager import IDManager
from infrasys.models import make_label, get_class_and_name_from_label


class ComponentManager:
    """Manages components"""

    def __init__(
        self,
        auto_add_composed_components: bool,
    ) -> None:
        self._components: dict[Type, dict[str | None, list[Component]]] = {}
        self._components_by_id: dict[int, Component] = {}
        self._components_by_uuid: dict[UUID, Component] = {}
        self._id_manager = IDManager(next_id=1)
        self._auto_add_composed_components = auto_add_composed_components
        self._associations = ComponentAssociations()

    @property
    def auto_add_composed_components(self) -> bool:
        """Return the setting for auto_add_composed_components."""
        return self._auto_add_composed_components

    @auto_add_composed_components.setter
    def auto_add_composed_components(self, val: bool) -> None:
        """Set auto_add_composed_components."""
        self._auto_add_composed_components = val

    def add(self, *components: Component, deserialization_in_progress=False) -> None:
        """Add one or more components to the system.

        Raises
        ------
        ISAlreadyAttached
            Raised if a component is already attached to a system.
        """
        if not components:
            return

        for component in components:
            self._add(component, deserialization_in_progress)

        self._associations.add(*components)

    def get(self, component_type: Type[Component], name: str) -> Any:
        """Return the component with the passed type and name.

        Raises
        ------
        ISDuplicateNames
            Raised if more than one component match the inputs.

        See Also
        --------
        list_by_name
        """
        if component_type not in self._components or name not in self._components[component_type]:
            label = make_label(component_type.__name__, name)
            msg = f"{label} is not stored"
            raise ISNotStored(msg)

        components = self._components[component_type][name]
        if len(components) > 1:
            msg = (
                f"There is more than one {component_type} with {name=}. Please use "
                "list_by_name instead."
            )
            raise ISOperationNotAllowed(msg)

        assert components
        return components[0]

    def get_num_components(self) -> int:
        """Return the number of stored components."""
        return len(self._components_by_id)

    def get_num_components_by_type(self) -> dict[Type, int]:
        """Return the number of stored components by type."""
        counts: dict[Type, int] = defaultdict(int)
        for component_type, components_by_type in self._components.items():
            for components_by_name in components_by_type.values():
                counts[component_type] += len(components_by_name)
        return counts

    def get_by_label(self, label: str) -> Any:
        """Return the component with the passed label.

        Raises
        ------
        ISOperationNotAllowed
            Raised if there is more than one matching component.
        """
        class_name, name_or_uuid = get_class_and_name_from_label(label)
        if isinstance(name_or_uuid, int):
            component = self.get_by_id(name_or_uuid)
            if type(component).__name__ == class_name:
                return component
            msg = f"No component with {label=} is stored."
            raise ISNotStored(msg)
        if isinstance(name_or_uuid, UUID):
            return self.get_by_uuid(name_or_uuid)

        for component_type, components_by_name in self._components.items():
            if component_type.__name__ == class_name:
                components = components_by_name.get(name_or_uuid)
                if components is None:
                    msg = f"No component with {label=} is stored."
                    raise ISNotStored(msg)
                if len(components) > 1:
                    msg = f"There is more than one component with {label=}."
                    raise ISOperationNotAllowed(msg)
                return components[0]

        msg = f"No component with {label=} is stored."
        raise ISNotStored(msg)

    def get_types(self) -> Iterable[Type[Component]]:
        """Return an iterable of all stored types."""
        return self._components.keys()

    def has_component(self, component) -> bool:
        """Return True if the component is attached."""
        return component.id in self._components_by_id if component.id is not None else False

    def iter(
        self, *component_types: Type[Component], filter_func: Callable | None = None
    ) -> Iterable[Any]:
        """Return the components with the passed type and optionally match filter_func.

        If component_type is an abstract type, all matching subtypes will be returned.
        """
        for component_type in component_types:
            yield from self._iter(component_type, filter_func)

    def _iter(
        self, component_type: Type[Component], filter_func: Callable | None
    ) -> Iterable[Any]:
        subclasses = component_type.__subclasses__()
        if subclasses:
            for subclass in subclasses:
                # Recurse.
                yield from self._iter(subclass, filter_func)

        if component_type in self._components:
            if filter_func is None:
                yield from itertools.chain(*self._components[component_type].values())
            else:
                for component in itertools.chain(*self._components[component_type].values()):
                    if filter_func(component):
                        yield component

    def list_by_name(self, component_type: Type[Component], name: str) -> list[Any]:
        """Return all components that match component_type and name.

        The component_type can be an abstract type.
        """
        return list(self.iter(component_type, filter_func=lambda x: x.name == name))

    def get_by_uuid(self, uuid: UUID) -> Any:
        """Return the component with the input UUID.

        Raises
        ------
        ISNotStored
            Raised if the UUID is not stored.
        """
        component = self._components_by_uuid.get(uuid)
        if component is None:
            msg = f"No component with {uuid=} is stored"
            raise ISNotStored(msg)
        return component

    def get_by_id(self, id_: int) -> Any:
        """Return the component with the input integer ID.

        Raises
        ------
        ISNotStored
            Raised if the ID is not stored.
        """
        component = self._components_by_id.get(id_)
        if component is None:
            msg = f"No component with id={id_} is stored"
            raise ISNotStored(msg)
        return component

    def iter_all(self) -> Iterable[Any]:
        """Return an iterator over all components."""
        return self._components_by_id.values()

    def list_child_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that this component composes."""
        return [
            self.get_by_id(x)
            for x in self._associations.list_child_components(
                component, component_type=component_type
            )
        ]

    def list_parent_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that compose this component."""
        return [
            self.get_by_id(x)
            for x in self._associations.list_parent_components(
                component, component_type=component_type
            )
        ]

    def to_records(
        self,
        component_type: Type[Component],
        filter_func: Callable | None = None,
        **kwargs,
    ) -> Iterable[dict]:
        """Return a dictionary representation of the requested components.

        For nested components we only return the label instead of the full component.
        """
        for component in self.iter(component_type, filter_func=filter_func):
            data = component.model_dump(**kwargs)
            for key in data:
                subcomponent = getattr(component, key)
                if issubclass(type(subcomponent), Component):
                    data[key] = subcomponent.label
                elif (
                    isinstance(subcomponent, list)
                    and subcomponent
                    and issubclass(type(subcomponent[0]), Component)
                ):
                    for i, sub_component_ in enumerate(subcomponent):
                        subcomponent[i] = sub_component_.label
            yield data

    def remove(self, component: Component, cascade_down: bool = True, force: bool = False) -> None:
        """Remove the component from the system.

        Notes
        -----
        Users should not call this directly. It should be called through the system
        so that time series is handled.
        """
        component_type = type(component)
        # The system method should have already performed the check, but for completeness in case
        # someone calls it directly, check here.
        key = component.name or component.label
        if component_type not in self._components or key not in self._components[component_type]:
            msg = f"{component.label} is not stored"
            raise ISNotStored(msg)

        self._check_parent_components_for_remove(component, force)
        container = self._components[component_type][key]
        for i, comp in enumerate(container):
            if comp.uuid == component.uuid:
                container.pop(i)
                if not self._components[component_type][key]:
                    self._components[component_type].pop(key)
                    if component.id is not None:
                        self._components_by_id.pop(component.id)
                    self._components_by_uuid.pop(component.uuid)
                if not self._components[component_type]:
                    self._components.pop(component_type)
                logger.debug("Removed component {}", component.label)
                if cascade_down:
                    child_components = self._associations.list_child_components(component)
                else:
                    child_components = []
                self._associations.remove(component)
                for child_id in child_components:
                    child = self.get_by_id(child_id)
                    parent_components = self.list_parent_components(child)
                    if not parent_components:
                        self.remove(child, cascade_down=cascade_down, force=force)
                return

        msg = f"Component {component.label} is not stored"
        raise ISNotStored(msg)

    def _check_parent_components_for_remove(self, component: Component, force: bool) -> None:
        parent_components = self.list_parent_components(component)
        if parent_components:
            parent_labels = ", ".join((x.label for x in parent_components))
            if force:
                logger.warning(
                    "Remove {} even though it is attached to these components: {}",
                    component.label,
                    parent_labels,
                )
            else:
                msg = (
                    f"Cannot remove {component.label} because it is attached to these components: "
                    f"{parent_labels}"
                )
                raise ISOperationNotAllowed(msg)

    def copy(
        self,
        component: Component,
        name: str | None = None,
        attach=False,
    ) -> Component:
        """Create a shallow copy of the component."""
        values = {}
        for field in type(component).model_fields:
            cur_val = getattr(component, field)
            if field == "name" and name:
                # Name is special-cased because it is a frozen field.
                val = name
            elif field in ("id", "uuid", "legacy_uuid"):
                continue
            else:
                val = cur_val
            values[field] = val

        new_component = type(component)(**values)  # type: ignore

        logger.info("Copied {} to {}", component.label, new_component.label)
        if attach:
            self.add(new_component)

        return new_component

    def deepcopy(self, component: Component) -> Component:
        """Create a deep copy of the component."""
        return copy.deepcopy(component)

    def change_uuid(self, component: Component) -> None:
        """Change the component UUID."""
        # TODO: would need to change the component UUID in time series and
        # supplemental attribute association tables.
        msg = "change_component_uuid"
        raise NotImplementedError(msg)

    def rebuild_component_associations(self) -> None:
        """Clear the component associations and rebuild the table. This may be necessary
        if a user reassigns connected components that are part of a system.
        """
        self._associations.clear()
        self._associations.add(*self.iter_all())
        logger.info("Rebuilt all component associations.")

    def update(
        self,
        component_type: Type[Component],
        update_func: Callable,
        filter_func: Callable | None = None,
    ) -> None:
        """Update multiple components of a given type."""

        for component in self.iter(component_type, filter_func=filter_func):
            update_func(component)
        return

    def _add(self, component: Component, deserialization_in_progress: bool) -> None:
        self.raise_if_attached(component)
        if not deserialization_in_progress:
            # TODO: Do we want any checks during deserialization? User could change the JSON.
            # We could prevent the user from changing the JSON with a checksum.
            self._check_component_addition(component)
            component.check_component_addition()
        if component.id is None:
            component.id = self._id_manager.get_next_id()
        elif component.id in self._components_by_id:
            msg = f"{component.label} with id={component.id} is already stored"
            raise ISAlreadyAttached(msg)
        else:
            self._id_manager.advance_past(component.id)

        if component.uuid in self._components_by_uuid:
            msg = f"{component.label} with legacy UUID={component.uuid} is already stored"
            raise ISAlreadyAttached(msg)

        cls = type(component)
        if cls not in self._components:
            self._components[cls] = {}

        name = component.name or component.label
        if name not in self._components[cls]:
            self._components[cls][name] = []

        self._components[cls][name].append(component)
        self._components_by_id[component.id] = component
        self._components_by_uuid[component.uuid] = component

        logger.debug("Added {} to the system", component.label)

    def _check_component_addition(self, component: Component) -> None:
        """Check all the fields of a component against the setting
        auto_add_composed_components. Recursive."""
        for field in type(component).model_fields:
            val = getattr(component, field)
            if isinstance(val, Component):
                self._handle_composed_component(val, parent_label=component.label)
                # Recurse.
                self._check_component_addition(val)
            elif isinstance(val, list) and val and isinstance(val[0], Component):
                for item in val:
                    self._handle_composed_component(item, parent_label=component.label)
                    # Recurse.
                    self._check_component_addition(item)

    def _handle_composed_component(
        self, component: Component, parent_label: str | None = None
    ) -> None:
        """Do what's needed for a composed component depending on system settings:
        nothing, add, or raise an exception.

        Parameters
        ----------
        component
            The composed (child) component.
        parent_label
            The label of the parent component that contains this composed component.
            Used to produce a clearer error message.
        """
        if component.id is not None and component.id in self._components_by_id:
            return

        if self._auto_add_composed_components:
            logger.debug("Auto-add composed component {}", component.label)
            self._add(component, False)
        else:
            parent = parent_label or component.label
            msg = (
                f"Component {parent} cannot be added to the system because "
                f"its composed component {component.label} is not already attached."
            )
            raise ISOperationNotAllowed(msg)

    def close(self) -> None:
        """Release resources held by the component manager."""
        try:
            self._associations.close()
        except Exception:
            logger.debug("Error closing component associations", exc_info=True)

    def raise_if_attached(self, component: Component):
        """Raise an exception if this component is attached to a system."""
        if component.id is not None and component.id in self._components_by_id:
            msg = f"{component.label} is already attached to the system"
            raise ISAlreadyAttached(msg)

    def raise_if_not_attached(self, component: Component):
        """Raise an exception if this component is not attached to a system.

        Parameters
        ----------
        system_uuid : UUID
            The component must be attached to the system with this UUID.
        """
        if component.id is None or component.id not in self._components_by_id:
            msg = f"{component.label} is not attached to the system"
            raise ISNotStored(msg)
