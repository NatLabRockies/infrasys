from typing import Optional, Type

from loguru import logger

from infrasys import COMPONENT_ASSOCIATIONS_TABLE, Component
from infrasys.exceptions import ISOperationNotAllowed
from infrasys.utils.classes import get_all_concrete_subclasses
from infrasys.utils.metadata_utils import create_component_associations_table
from infrasys.utils.sqlite import create_in_memory_db, execute


class ComponentAssociations:
    """Stores associations between components. Allows callers to quickly find components composed
    by other components, such as the generator to which a bus is connected."""

    def __init__(self) -> None:
        # This uses a different database because it is not persisted when the system
        # is saved to files. It will be rebuilt during de-serialization.
        self._con = create_in_memory_db(":memory:")
        self._closed = False
        create_component_associations_table(self._con, table_name=COMPONENT_ASSOCIATIONS_TABLE)

    def add(self, *components: Component):
        """Store an association between each component and directly attached subcomponents.

        - Inspects the type of each field of each component's type. Looks for subtypes of
          Component and lists of subtypes of Component.
        - Does not consider component fields that are dictionaries or other data structures.
        """
        rows = []
        for component in components:
            for field in type(component).model_fields:
                val = getattr(component, field)
                if isinstance(val, Component):
                    rows.append(self._make_row(component, val))
                elif isinstance(val, list) and val and isinstance(val[0], Component):
                    for item in val:
                        rows.append(self._make_row(component, item))

        if rows:
            self._insert_rows(rows)

    def clear(self) -> None:
        """Clear all component associations."""
        execute(self._con.cursor(), f"DELETE FROM {COMPONENT_ASSOCIATIONS_TABLE}")
        logger.info("Cleared all component associations.")

    def list_child_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[int]:
        """Return a list of all component IDs that this component composes.
        For example, return the bus attached to a generator.
        """
        if component.id is None:
            msg = f"{component.label} does not have an id assigned."
            raise ISOperationNotAllowed(msg)
        where_clause = "WHERE component_id = ?"
        params = [component.id]
        if component_type is not None:
            res = _make_params_and_where_clause(component_type, "attached_component_type")
            params.extend(res[0])
            where_clause += res[1]
        query = (
            f"SELECT attached_component_id FROM {COMPONENT_ASSOCIATIONS_TABLE} {where_clause}"
        )
        cur = self._con.cursor()
        return [x[0] for x in execute(cur, query, params)]

    def list_parent_components(
        self, component: Component, component_type: Optional[Type[Component]] = None
    ) -> list[int]:
        """Return a list of all component IDs that compose this component.
        For example, return all components connected to a bus.
        """
        if component.id is None:
            msg = f"{component.label} does not have an id assigned."
            raise ISOperationNotAllowed(msg)
        where_clause = "WHERE attached_component_id = ?"
        params = [component.id]
        if component_type is not None:
            res = _make_params_and_where_clause(component_type, "component_type")
            params.extend(res[0])
            where_clause += res[1]
        query = f"SELECT component_id FROM {COMPONENT_ASSOCIATIONS_TABLE} {where_clause}"
        cur = self._con.cursor()
        return [x[0] for x in execute(cur, query, params)]

    def remove(self, component: Component) -> None:
        """Delete all rows with this component."""
        if component.id is None:
            msg = f"{component.label} does not have an id assigned."
            raise ISOperationNotAllowed(msg)
        query = f"""
            DELETE
            FROM {COMPONENT_ASSOCIATIONS_TABLE}
            WHERE component_id = ? OR attached_component_id = ?
        """
        params = [component.id, component.id]
        execute(self._con.cursor(), query, params)
        logger.debug("Removed all associations with component {}", component.label)

    def close(self) -> None:
        """Close the backing SQLite connection."""
        if self._closed:
            return
        try:
            self._con.close()
        finally:
            self._closed = True

    def _insert_rows(self, rows: list[tuple]) -> None:
        cur = self._con.cursor()
        placeholder = ",".join(["?"] * len(rows[0]))
        component_ids = {(row[1],) for row in rows}.union({(row[3],) for row in rows})
        cur.executemany("INSERT OR IGNORE INTO components(id) VALUES(?)", component_ids)
        query = f"INSERT INTO {COMPONENT_ASSOCIATIONS_TABLE} VALUES({placeholder})"
        try:
            cur.executemany(query, rows)
        finally:
            self._con.commit()

    @staticmethod
    def _make_row(component: Component, attached_component: Component):
        if component.id is None:
            msg = f"{component.label} does not have an id assigned."
            raise ISOperationNotAllowed(msg)
        if attached_component.id is None:
            msg = f"{attached_component.label} does not have an id assigned."
            raise ISOperationNotAllowed(msg)
        return (
            None,
            component.id,
            type(component).__name__,
            attached_component.id,
            type(attached_component).__name__,
        )


def _make_params_and_where_clause(
    component_type: Type[Component], field: str
) -> tuple[list[str], str]:
    params = _make_type_params(component_type)
    if len(params) == 1:
        where_clause = f" AND {field} = ?"
    else:
        where_clause = f" AND {field} IN ({','.join(['?'] * len(params))})"
    return params, where_clause


def _make_type_params(component_type: Type[Component]) -> list[str]:
    params: list[str] = []
    subclasses = get_all_concrete_subclasses(component_type) or [component_type]
    for cls in subclasses:
        params.append(cls.__name__)  # type: ignore
    return params
