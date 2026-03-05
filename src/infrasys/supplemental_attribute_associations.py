"""Stores supplemental attribute associations in SQLite database"""

import sqlite3
from typing import Any, Optional, Sequence
from uuid import UUID

from loguru import logger

from infrasys import Component, SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE
from infrasys.exceptions import ISAlreadyAttached
from infrasys.supplemental_attribute import SupplementalAttribute
from infrasys.utils.sqlite import execute
from infrasys.utils.metadata_utils import (
    create_supplemental_attribute_associations_table,
)

TABLE_NAME = SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE


class SupplementalAttributeAssociationsStore:
    """Stores supplemental attribute associations in a SQLite database."""

    def __init__(self, con: sqlite3.Connection, initialize: bool = True):
        self._con = con
        if initialize:
            create_supplemental_attribute_associations_table(self._con, table_name=TABLE_NAME)

    _ADD_ASSOCIATION_QUERY = f"""
        SELECT id FROM {TABLE_NAME}
        WHERE attribute_uuid = ? AND component_uuid = ?
        LIMIT 1
    """
    _INSERT_ASSOCIATION_QUERY = f"""
        INSERT INTO {TABLE_NAME} (
            id,
            attribute_uuid,
            attribute_type,
            component_uuid,
            component_type
        ) VALUES (?, ?, ?, ?, ?)
    """

    def add(
        self,
        component: Component,
        attribute: SupplementalAttribute,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Add association to the database.

        Raises
        ------
        ISAlreadyAttached
            Raised if the supplemental attribute association is already stored.
        """
        con = connection or self._con
        params = (str(attribute.uuid), str(component.uuid))
        cur = con.cursor()
        res = execute(cur, self._ADD_ASSOCIATION_QUERY, params=params).fetchone()
        if res:
            msg = f"An association with {component=} {attribute=} is already stored."
            raise ISAlreadyAttached(msg)

        row = (
            None,
            str(attribute.uuid),
            type(attribute).__name__,
            str(component.uuid),
            type(component).__name__,
        )
        execute(cur, self._INSERT_ASSOCIATION_QUERY, params=row)
        if connection is None:
            self._con.commit()

    _HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_QUERY = f"""
        SELECT id FROM {TABLE_NAME}
        WHERE attribute_uuid = ? AND component_uuid = ?
        LIMIT 1
    """

    def has_association_by_component_and_attribute(
        self,
        component: Component,
        attribute: SupplementalAttribute,
    ) -> bool:
        """Return True if the component and supplemental attribute have an association."""
        params = (str(attribute.uuid), str(component.uuid))
        return self._has_rows(self._HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_QUERY, params)

    _HAS_ASSOCIATION_BY_ATTRIBUTE_QUERY = f"SELECT id FROM {TABLE_NAME} WHERE attribute_uuid = ?"

    def has_association_by_attribute(self, attribute: SupplementalAttribute) -> bool:
        """Return true if there is at least one association matching the inputs."""
        # Note: Unlike the other has_association methods, this is not covered by an index.
        params = (str(attribute.uuid),)
        return self._has_rows(self._HAS_ASSOCIATION_BY_ATTRIBUTE_QUERY, params)

    _HAS_ASSOCIATION_BY_COMPONENT_QUERY = f"SELECT id FROM {TABLE_NAME} WHERE component_uuid = ?"

    def has_association_by_component(self, component: Component) -> bool:
        """Return True if there is at least one association with the component."""
        params = (str(component.uuid),)
        return self._has_rows(self._HAS_ASSOCIATION_BY_COMPONENT_QUERY, params)

    _HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_TYPE_QUERY = f"""
        SELECT attribute_uuid
        FROM {TABLE_NAME}
        WHERE component_uuid = ? AND attribute_type = ?
        LIMIT 1
    """

    def has_association_by_component_and_attribute_type(
        self, component: Component, attribute_type: str
    ) -> bool:
        """Return True if the component has an association with a supplemental attribute of the
        given type.
        """
        params = (str(component.uuid), attribute_type)
        return self._has_rows(self._HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_TYPE_QUERY, params)

    def _has_rows(self, query: str, params: Sequence[Any]) -> bool:
        cur = self._con.cursor()
        res = execute(cur, query, params=params).fetchone()
        return res is not None

    _LIST_ASSOCIATED_COMPONENT_UUIDS_QUERY = f"""
        SELECT component_uuid
        FROM {TABLE_NAME}
        WHERE attribute_uuid = ?
    """

    def list_associated_component_uuids(self, attribute: SupplementalAttribute) -> list[UUID]:
        """Return the component UUIDs associated with the attribute."""
        params = (str(attribute.uuid),)
        cur = self._con.cursor()
        rows = execute(cur, self._LIST_ASSOCIATED_COMPONENT_UUIDS_QUERY, params=params)
        return [UUID(x[0]) for x in rows]

    _LIST_ASSOCIATED_SUPPLEMENTAL_ATTRIBUTE_UUIDS_QUERY1 = f"""
        SELECT attribute_uuid
        FROM {TABLE_NAME}
        WHERE component_uuid = ?
    """
    _LIST_ASSOCIATED_SUPPLEMENTAL_ATTRIBUTE_UUIDS_QUERY2 = f"""
        SELECT attribute_uuid
        FROM {TABLE_NAME}
        WHERE attribute_type = ? AND component_uuid = ?
    """

    def list_associated_supplemental_attribute_uuids(
        self,
        component: Component,
        attribute_type: Optional[str] = None,
    ) -> list[UUID]:
        """Return the supplemental attribute UUIDs associated with the component and attribute
        type.
        """
        if attribute_type is None:
            query = self._LIST_ASSOCIATED_SUPPLEMENTAL_ATTRIBUTE_UUIDS_QUERY1
            params = (str(component.uuid),)
        else:
            query = self._LIST_ASSOCIATED_SUPPLEMENTAL_ATTRIBUTE_UUIDS_QUERY2
            params = (attribute_type, str(component.uuid))
        cur = self._con.cursor()
        rows = execute(cur, query, params=params)
        return [UUID(x[0]) for x in rows]

    def remove_association_by_attribute(
        self,
        attribute: SupplementalAttribute,
        must_exist: bool = True,
    ) -> None:
        """Remove all associations with the given attribute."""
        where_clause = "WHERE attribute_uuid = ?"
        params = (str(attribute.uuid),)
        num_deleted = self._remove_associations(where_clause, params)
        if must_exist and num_deleted < 1:
            msg = f"Bug: unexpected number of deletions: {num_deleted}. Should have been >= 1."
            raise Exception(msg)

    def remove_association(
        self,
        component: Component,
        attribute: SupplementalAttribute,
    ) -> None:
        """Remove the association between the attribute and component."""
        where_clause = "WHERE attribute_uuid = ? AND component_uuid = ?"
        params = (str(attribute.uuid), str(component.uuid))
        num_deleted = self._remove_associations(where_clause, params)
        if num_deleted != 1:
            msg = f"Bug: unexpected number of deletions: {num_deleted}. Should have been 1."
            raise Exception(msg)

    # This functionality, copied from Sienna, could be added if needed.
    # def remove_associations(self, attribute_type: str) -> None:
    #    """Remove all associations of the given type."""
    #    where_clause = "WHERE attribute_type = ?"
    #    params = (attribute_type,)
    #    num_deleted = self._remove_associations(where_clause, params)
    #    logger.debug("Deleted %s supplemental attribute associations", num_deleted)

    def _remove_associations(self, where_clause: str, params: Sequence[Any]) -> int:
        query = f"DELETE FROM {TABLE_NAME} {where_clause}"
        cur = self._con.cursor()
        execute(cur, query, params)
        rows = execute(cur, "SELECT CHANGES() AS changes").fetchall()
        assert len(rows) == 1, rows
        row = rows[0]
        logger.debug("Deleted {} rows from the supplemental attribute associations table", row[0])
        self._con.commit()
        return row[0]

    _GET_ATTRIBUTE_COUNTS_BY_TYPE_QUERY = f"""
        SELECT
            attribute_type
            ,count(*) AS count
        FROM {TABLE_NAME}
        GROUP BY
            attribute_type
        ORDER BY
            attribute_type
    """

    def get_attribute_counts_by_type(self) -> list[dict[str, Any]]:
        """Return a list of dicts of stored attribute counts by type."""
        cur = self._con.cursor()
        rows = execute(cur, self._GET_ATTRIBUTE_COUNTS_BY_TYPE_QUERY).fetchall()
        return [{"type": x[0], "count": x[1]} for x in rows]

    # TODO: This could be useful if we want to display a table to users. We don't yet
    # directly depend on Pandas. We could add that dependency or use some other table display.
    # This was copied from InfrastructureSystems.jl.
    # def get_attribute_summary_table(self) -> pd.DataFrame:
    #    """Return a DataFrame with the number of supplemental attributes by type for components."""
    #    query = f"""
    #        SELECT
    #            attribute_type
    #            ,component_type
    #            ,count(*) AS count
    #        FROM {self.TABLE_NAME}
    #        GROUP BY
    #            attribute_type
    #            ,component_type
    #        ORDER BY
    #            attribute_type
    #            ,component_type
    #    """
    #    cur = self._con.cursor()
    #    rows = execute(cur, query).fetchall()
    #    #return DataFrame(_execute(associations, query))

    _GET_NUM_ATTRIBUTES_QUERY = f"""
            SELECT COUNT(DISTINCT attribute_uuid) AS count
            FROM {TABLE_NAME}
        """

    def get_num_attributes(self) -> int:
        """Return the number of supplemental attributes."""
        cur = self._con.cursor()
        return execute(cur, self._GET_NUM_ATTRIBUTES_QUERY).fetchone()[0]

    _GET_NUM_COMPONENTS_WITH_ATTRIBUTES_QUERY = f"""
        SELECT COUNT(DISTINCT component_uuid) AS count
        FROM {TABLE_NAME}
    """

    def get_num_components_with_attributes(self) -> int:
        """Return the number of components with supplemental attributes."""
        cur = self._con.cursor()
        return execute(cur, self._GET_NUM_COMPONENTS_WITH_ATTRIBUTES_QUERY).fetchone()[0]
