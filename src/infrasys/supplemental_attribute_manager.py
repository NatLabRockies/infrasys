"""Manages supplemental"""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Generator, Iterable, Optional, Type, TypeVar, cast

from loguru import logger
from infrastore import (
    DuplicateAssociationError,
    SupplementalAttributeAssociation,
    Store,
)

if TYPE_CHECKING:
    from infrasys.time_series_store_storage import TimeSeriesStoreStorage

from infrasys.component import Component
from infrasys.exceptions import ISAlreadyAttached, ISNotStored, ISOperationNotAllowed
from infrasys.id_manager import IDManager
from infrasys.supplemental_attribute import SupplementalAttribute

T = TypeVar("T", bound="SupplementalAttribute")


class SupplementalAttributeManager:
    """Manages supplemental attributes"""

    def __init__(self, storage: "TimeSeriesStoreStorage", id_manager: IDManager, **kwargs) -> None:
        self._storage = storage
        self._attributes: dict[Type, dict[int, SupplementalAttribute]] = {}
        self._attributes_by_id: dict[int, SupplementalAttribute] = {}
        # Shared with the system's other managers so that every stored object draws from
        # one stream of IDs.
        self._id_manager = id_manager
        self._in_context = False
        self._context_new_attributes: list[SupplementalAttribute] = []
        self._context_removed_attributes: list[SupplementalAttribute] = []

    @property
    def _store(self) -> Store:
        """Resolve the store on every access rather than caching it.

        The storage closes and reopens its files when serializing, which yields a new
        handle, so a reference captured here would go stale after the first save.
        """
        return self._storage.store

    def add(
        self,
        component: Optional[Component],
        attribute: SupplementalAttribute,
        deserialization_in_progress=False,
    ) -> None:
        """Add one or more supplemental attributes to the system.

        Raises
        ------
        ISAlreadyAttached
            Raised if a component is already attached to a system.
        """
        if component is None and not deserialization_in_progress:
            msg = "component can only be None when deserialization_in_progress"
            raise Exception(msg)

        already_attached = self.has_attribute(attribute)
        if not deserialization_in_progress and not already_attached:
            attribute.check_supplemental_attribute_addition()

        if not already_attached:
            self._store_attribute(attribute)

        try:
            if component is not None:
                self._add_association(component, attribute)
        except Exception:
            if not already_attached:
                self.rollback_attribute_addition(attribute)
            raise

        if self._in_context and not already_attached:
            self._context_new_attributes.append(attribute)

    def _store_attribute(self, attribute: SupplementalAttribute) -> None:
        """Assign an ID if needed and index the attribute in memory."""
        if attribute.id is None:
            attribute.id = self._id_manager.get_next_id()
        elif attribute.id in self._attributes_by_id:
            msg = f"{attribute.label} with id={attribute.id} is already stored"
            raise ISAlreadyAttached(msg)
        else:
            self._id_manager.advance_past(attribute.id)

        self._attributes.setdefault(type(attribute), {})[attribute.id] = attribute
        self._attributes_by_id[attribute.id] = attribute

    def _add_association(self, component: Component, attribute: SupplementalAttribute) -> None:
        association = SupplementalAttributeAssociation(
            _get_component_id(component),
            type(component).__name__,
            _get_attribute_id(attribute),
            type(attribute).__name__,
        )
        try:
            self._store.add_supplemental_attribute_association(association)
        except DuplicateAssociationError:
            msg = f"An association with {component=} {attribute=} is already stored."
            raise ISAlreadyAttached(msg)

    @contextmanager
    def open_metadata_store(self) -> Generator[Store, None, None]:
        """Open a transactional metadata context for supplemental attributes.

        Notes
        -----
        Nested metadata contexts are disallowed. If a nested context attempt raises
        and the exception escapes this context manager, all metadata updates already
        performed in this context are rolled back.

        The backing store has no transaction primitive, so the association rows are
        snapshotted on entry and restored verbatim if an exception escapes the context.
        Association rows are small metadata, so a full snapshot is cheap.
        """
        if self._in_context:
            msg = "Cannot nest open_metadata_store contexts."
            raise ISOperationNotAllowed(msg)

        snapshot = self._store.list_supplemental_attribute_associations()
        self._in_context = True
        self._context_new_attributes = []
        self._context_removed_attributes = []
        try:
            yield self._store
        except Exception:
            self._restore_associations(snapshot)
            self._rollback_new_attributes()
            self._rollback_removed_attributes()
            raise
        finally:
            self._in_context = False
            self._context_new_attributes = []
            self._context_removed_attributes = []

    def _restore_associations(self, snapshot: list[SupplementalAttributeAssociation]) -> None:
        """Replace all stored associations with the ones captured in the snapshot."""
        self._store.remove_supplemental_attribute_associations()
        if snapshot:
            self._store.add_supplemental_attribute_associations(snapshot)

    def _rollback_new_attributes(self) -> None:
        for attribute in self._context_new_attributes:
            self.rollback_attribute_addition(attribute)

    def _rollback_removed_attributes(self) -> None:
        # Association rows are restored by self._restore_associations() before this
        # method runs. This only repairs in-memory attribute bookkeeping.
        for attribute in self._context_removed_attributes:
            attr_type = type(attribute)
            if attr_type not in self._attributes:
                self._attributes[attr_type] = {}
            assert attribute.id is not None
            self._attributes[attr_type][attribute.id] = attribute
            self._attributes_by_id[attribute.id] = attribute

    def rollback_attribute_addition(self, attribute: SupplementalAttribute) -> None:
        """Remove an attribute from in-memory cache without modifying DB associations."""
        attr_type = type(attribute)
        attrs = self._attributes.get(attr_type)
        if attrs is None:
            return
        if attribute.id is not None:
            attrs.pop(attribute.id, None)
            self._attributes_by_id.pop(attribute.id, None)
        if not attrs:
            self._attributes.pop(attr_type, None)

    def get_attribute_counts_by_type(self) -> list[dict[str, Any]]:
        """Return a list of dicts of stored attribute counts by type."""
        return [
            {"type": type_, "count": count}
            for type_, count in self._store.supplemental_attribute_counts_by_type()
        ]

    def get_num_attributes(self) -> int:
        """Return the number of supplemental attributes."""
        return self._store.count_supplemental_attributes()

    def get_num_components_with_attributes(self) -> int:
        """Return the number of components with supplemental attributes."""
        return self._store.count_components_with_attributes()

    def get_component_ids_with_attribute(self, attribute: SupplementalAttribute) -> list[int]:
        """Return all component IDs attached to the given attribute."""
        return self._store.list_components_with_attributes(
            attribute_id=_get_attribute_id(attribute)
        )

    def get_by_id(self, id_: int) -> SupplementalAttribute:
        """Return the supplemental attribute with the given integer ID.

        Raises
        ------
        ISNotStored
            Raised if the ID is not stored.
        """
        attr = self._attributes_by_id.get(id_)
        if attr is None:
            msg = f"No supplemental attribute with id={id_} is stored"
            raise ISNotStored(msg)
        return attr

    def get_attributes_with_component(
        self,
        component: Component,
        attribute_type: Optional[Type[T]] = None,
        filter_func: Optional[Callable[[T], bool]] = None,
    ) -> list[T]:
        attribute_types = None if attribute_type is None else [attribute_type.__name__]
        attribute_ids = self._store.list_supplemental_attribute_ids(
            component_id=_get_component_id(component),
            attribute_types=attribute_types,
        )
        attributes: list[T] = []
        for id_ in attribute_ids:
            attribute = cast(T, self.get_by_id(id_))
            if filter_func is None or filter_func(attribute):
                attributes.append(attribute)
        return attributes

    def has_attribute(self, attribute: SupplementalAttribute) -> bool:
        # IDs are only unique within one system, so an attribute from a different system
        # can collide with a local ID. Identity is the discriminator.
        if attribute.id is None:
            return False
        attributes = self._attributes.get(type(attribute))
        return attributes is not None and attributes.get(attribute.id) is attribute

    def has_association(self, component: Component, attribute: SupplementalAttribute) -> bool:
        """Return True if the component and supplemental attribute have an association."""
        return self._store.has_supplemental_attribute_association(
            component_id=_get_component_id(component),
            attribute_id=_get_attribute_id(attribute),
        )

    def has_association_by_type(
        self,
        component: Component,
        attribute_type: Optional[Type[SupplementalAttribute]] = None,
    ) -> bool:
        """Return true if the component has an association with a supplemental attribute,
        optionally with the given type.
        """
        attribute_types = None if attribute_type is None else [attribute_type.__name__]
        return self._store.has_supplemental_attribute_association(
            component_id=_get_component_id(component),
            attribute_types=attribute_types,
        )

    def remove(
        self, attribute: SupplementalAttribute, association_must_exist: bool = True
    ) -> None:
        """Remove the supplemental attribute from the system.

        Notes
        -----
        Users should not call this directly. It should be called through the system
        so that time series is handled.
        """
        self.raise_if_not_attached(attribute)
        num_deleted = self._store.remove_supplemental_attribute_associations(
            attribute_id=_get_attribute_id(attribute)
        )
        if association_must_exist and num_deleted < 1:
            msg = f"Bug: unexpected number of deletions: {num_deleted}. Should have been >= 1."
            raise Exception(msg)
        attr_type = type(attribute)
        if self._in_context:
            self._context_removed_attributes.append(attribute)
        assert attribute.id is not None
        self._attributes[attr_type].pop(attribute.id)
        self._attributes_by_id.pop(attribute.id, None)
        if not self._attributes[attr_type]:
            self._attributes.pop(attr_type)
        logger.debug("Removed supplemental attribute {}", attribute.label)

    def remove_attribute_from_component(
        self, component: Component, attribute: SupplementalAttribute
    ) -> None:
        """Remove the supplemental attribute from the component. If the attribute is not attached
        to any other components, remove it from the system.

        Notes
        -----
        Users should not call this directly. It should be called through the system
        so that time series is handled.
        """
        self.raise_if_not_attached(attribute)
        attribute_id = _get_attribute_id(attribute)
        num_deleted = self._store.remove_supplemental_attribute_associations(
            component_id=_get_component_id(component),
            attribute_id=attribute_id,
        )
        if num_deleted != 1:
            msg = f"Bug: unexpected number of deletions: {num_deleted}. Should have been 1."
            raise Exception(msg)
        if not self._store.has_supplemental_attribute_association(attribute_id=attribute_id):
            self.remove(attribute, association_must_exist=False)

    def iter(
        self,
        *attribute_types: Type[T],
        filter_func: Optional[Callable[[T], bool]] = None,
    ) -> Generator[SupplementalAttribute, None, None]:
        for attr_type in attribute_types:
            yield from self._iter(attr_type, filter_func)

    def iter_all(self) -> Iterable[Any]:
        """Return an iterator over all components."""
        for attr_dict in self._attributes.values():
            yield from attr_dict.values()

    def _iter(
        self,
        attr_type: Type[T],
        filter_func: Optional[Callable[[T], bool]] = None,
    ) -> Generator[Any, None, None]:
        subclasses = attr_type.__subclasses__()
        if subclasses:
            for subclass in subclasses:
                # Recurse.
                yield from self._iter(subclass, filter_func=filter_func)

        if attr_type in self._attributes:
            for val in self._attributes[attr_type].values():
                if filter_func is None or filter_func(val):  # type: ignore
                    yield val

    def raise_if_attached(self, attribute: SupplementalAttribute):
        """Raise an exception if this attribute is attached to a system."""
        if self.has_attribute(attribute):
            msg = f"{attribute.label} is already attached to the system"
            raise ISAlreadyAttached(msg)

    def raise_if_not_attached(self, attribute: SupplementalAttribute):
        """Raise an exception if this attribute is not attached to a system."""
        if not self.has_attribute(attribute):
            msg = f"{attribute.label} is not attached to the system"
            raise ISNotStored(msg)


def _get_component_id(component: Component) -> int:
    if component.id is None:
        msg = f"{component.label} does not have an id assigned."
        raise ISOperationNotAllowed(msg)
    return component.id


def _get_attribute_id(attribute: SupplementalAttribute) -> int:
    if attribute.id is None:
        msg = f"{attribute.label} does not have an id assigned."
        raise ISOperationNotAllowed(msg)
    return attribute.id
