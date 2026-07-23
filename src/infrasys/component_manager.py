"""Manages components"""

import copy
import itertools
from collections import defaultdict
from typing import Any, Callable, Iterable, Optional, Type
from uuid import UUID
from loguru import logger
from castore import ParentChildAssociation, Store

from infrasys.component import Component
from infrasys.exceptions import (
    ISAlreadyAttached,
    ISNotStored,
    ISOperationNotAllowed,
)
from infrasys.id_manager import IDManager
from infrasys.models import make_label, get_class_and_name_from_label
from infrasys.utils.classes import get_all_concrete_subclasses


class ComponentManager:
    """Manages components"""

    def __init__(
        self,
        auto_add_composed_components: bool,
        store: Store,
    ) -> None:
        self._components: dict[Type, dict[str | None, list[Component]]] = {}
        self._components_by_id: dict[int, Component] = {}
        self._components_by_uuid: dict[UUID, Component] = {}
        self._id_manager = IDManager(next_id=1)
        self._auto_add_composed_components = auto_add_composed_components
        self._store = store

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

        if not deserialization_in_progress:
            # Associations are persisted alongside the time series data and so they are
            # already present when a system is deserialized.
            self.add_associations(*components)

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
        if isinstance(name_or_uuid, UUID):
            return self.get_by_uuid(name_or_uuid)

        # Try name-based lookup first (handles numeric component names like "123").
        # Only falls through to ID-based lookup when no name match is found.
        if isinstance(name_or_uuid, str):
            for component_type, components_by_name in self._components.items():
                if component_type.__name__ == class_name:
                    components = components_by_name.get(name_or_uuid)
                    if components is not None:
                        if len(components) > 1:
                            msg = f"There is more than one component with {label=}."
                            raise ISOperationNotAllowed(msg)
                        return components[0]
            # Name not found; try to parse as integer ID.
            try:
                component_id = int(name_or_uuid)
            except ValueError:
                msg = f"No component with {label=} is stored."
                raise ISNotStored(msg)

            component = self.get_by_id(component_id)
            if type(component).__name__ == class_name:
                return component

        msg = f"No component with {label=} is stored."
        raise ISNotStored(msg)

    def get_types(self) -> Iterable[Type[Component]]:
        """Return an iterable of all stored types."""
        return self._components.keys()

    def has_component(self, component) -> bool:
        """Return True if the component is attached."""
        if component.id is None:
            return False
        stored_component = self._components_by_id.get(component.id)
        return stored_component is not None and _component_matches(stored_component, component)

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

    def add_associations(self, *components: Component) -> None:
        """Store an association between each component and its directly attached subcomponents.

        - Inspects the type of each field of each component's type. Looks for subtypes of
          Component and lists of subtypes of Component.
        - Does not consider component fields that are dictionaries or other data structures.
        """
        associations: list[ParentChildAssociation] = []
        seen: set[tuple[int, int]] = set()
        for component in components:
            for field in type(component).model_fields:
                val = getattr(component, field)
                if isinstance(val, Component):
                    children = [val]
                elif isinstance(val, list) and val and isinstance(val[0], Component):
                    children = val
                else:
                    continue
                for child in children:
                    association = self._make_association(component, child)
                    # A component may reference the same child from more than one field.
                    # The store rejects duplicates, so de-duplicate here.
                    pair = (association.parent_id, association.child_id)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    associations.append(association)

        if associations:
            self._store.add_parent_child_associations(associations)

    def clear_associations(self) -> None:
        """Clear all component associations."""
        self._store.remove_parent_child_associations()
        logger.info("Cleared all component associations.")

    def remove_associations(self, component: Component) -> None:
        """Remove all associations that reference this component in either direction."""
        component_id = _get_id(component)
        self._store.remove_parent_child_associations(parent_id=component_id)
        self._store.remove_parent_child_associations(child_id=component_id)
        logger.debug("Removed all associations with component {}", component.label)

    def list_child_component_ids(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[int]:
        """Return the IDs of all components that this component composes."""
        return self._store.list_children(
            parent_id=_get_id(component),
            child_types=_make_type_names(component_type),
        )

    def list_parent_component_ids(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[int]:
        """Return the IDs of all components that compose this component."""
        return self._store.list_parents(
            child_id=_get_id(component),
            parent_types=_make_type_names(component_type),
        )

    def list_child_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that this component composes."""
        self.raise_if_not_attached(component)
        return [
            self.get_by_id(x)
            for x in self.list_child_component_ids(component, component_type=component_type)
        ]

    def list_parent_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[Component]:
        """Return a list of all components that compose this component."""
        self.raise_if_not_attached(component)
        return [
            self.get_by_id(x)
            for x in self.list_parent_component_ids(component, component_type=component_type)
        ]

    @staticmethod
    def _make_association(parent: Component, child: Component) -> ParentChildAssociation:
        return ParentChildAssociation(
            _get_id(parent),
            type(parent).__name__,
            _get_id(child),
            type(child).__name__,
        )

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

        container = self._components[component_type][key]
        matches = [
            (i, comp) for i, comp in enumerate(container) if _component_matches(comp, component)
        ]
        if not matches:
            msg = f"Component {component.label} is not stored"
            raise ISNotStored(msg)
        matched_index, matched_component = matches[0]
        self._check_parent_components_for_remove(matched_component, force)
        container.pop(matched_index)
        # Always clean up ID/UUID indexes for the removed component,
        # regardless of whether other components remain under the same key.
        if matched_component.id is not None:
            self._components_by_id.pop(matched_component.id, None)
        self._components_by_uuid.pop(matched_component.uuid, None)
        if not self._components[component_type][key]:
            self._components[component_type].pop(key)
        if not self._components[component_type]:
            self._components.pop(component_type)
        logger.debug("Removed component {}", matched_component.label)
        if cascade_down:
            child_components = self.list_child_component_ids(matched_component)
        else:
            child_components = []
        self.remove_associations(matched_component)
        for child_id in child_components:
            child = self.get_by_id(child_id)
            parent_components = self.list_parent_components(child)
            if not parent_components:
                self.remove(child, cascade_down=cascade_down, force=force)
        return

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
        self.clear_associations()
        self.add_associations(*self.iter_all())
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
        if self.has_component(component):
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
        """Release resources held by the component manager.

        Associations live in the time series store, which is closed by its own manager, so
        there is nothing to release here.
        """

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
        if not self.has_component(component):
            msg = f"{component.label} is not attached to the system"
            raise ISNotStored(msg)


def _get_id(component: Component) -> int:
    """Return the component's ID, raising if one has not been assigned."""
    if component.id is None:
        msg = f"{component.label} does not have an id assigned."
        raise ISOperationNotAllowed(msg)
    return component.id


def _make_type_names(component_type: Optional[Type[Component]]) -> list[str] | None:
    """Expand a possibly-abstract component type into concrete type names."""
    if component_type is None:
        return None
    subclasses = get_all_concrete_subclasses(component_type) or [component_type]
    return [cls.__name__ for cls in subclasses]


def _component_matches(a: Component, b: Component) -> bool:
    """Return True if two component references identify the same stored component."""
    if a.id is None or b.id is None:
        return False
    return a.id == b.id and a.uuid == b.uuid
