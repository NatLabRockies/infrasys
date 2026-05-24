"""Stores supplemental attribute associations in SQLite database"""

import sqlite3
from typing import Any, Optional, Sequence

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

    _CHECK_EXISTING_ASSOCIATION_QUERY = f"""
        SELECT id FROM {TABLE_NAME}
        WHERE attribute_id = ? AND component_id = ?
        LIMIT 1
    """
    _INSERT_ASSOCIATION_QUERY = f"""
        INSERT INTO {TABLE_NAME} (
            id,
            attribute_id,
            attribute_type,
            component_id,
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
        assert attribute.id is not None
        assert component.id is not None
        params = (attribute.id, component.id)
        cur = con.cursor()
        res = execute(cur, self._CHECK_EXISTING_ASSOCIATION_QUERY, params=params).fetchone()
        if res:
            msg = f"An association with {component=} {attribute=} is already stored."
            raise ISAlreadyAttached(msg)

        row = (
            None,
            attribute.id,
            type(attribute).__name__,
            component.id,
            type(component).__name__,
        )
        self._insert_parent_ids(cur, component.id, attribute.id)
        execute(cur, self._INSERT_ASSOCIATION_QUERY, params=row)
        if connection is None:
            self._con.commit()

    _HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_QUERY = f"""
        SELECT id FROM {TABLE_NAME}
        WHERE attribute_id = ? AND component_id = ?
        LIMIT 1
    """

    def has_association_by_component_and_attribute(
        self,
        component: Component,
        attribute: SupplementalAttribute,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Return True if the component and supplemental attribute have an association."""
        assert attribute.id is not None
        assert component.id is not None
        params = (attribute.id, component.id)
        return self._has_rows(
            self._HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_QUERY,
            params,
            connection=connection,
        )

    _HAS_ASSOCIATION_BY_ATTRIBUTE_QUERY = f"SELECT id FROM {TABLE_NAME} WHERE attribute_id = ?"

    def has_association_by_attribute(
        self,
        attribute: SupplementalAttribute,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Return true if there is at least one association matching the inputs."""
        # Note: Unlike the other has_association methods, this is not covered by an index.
        assert attribute.id is not None
        params = (attribute.id,)
        return self._has_rows(
            self._HAS_ASSOCIATION_BY_ATTRIBUTE_QUERY,
            params,
            connection=connection,
        )

    _HAS_ASSOCIATION_BY_COMPONENT_QUERY = f"SELECT id FROM {TABLE_NAME} WHERE component_id = ?"

    def has_association_by_component(
        self,
        component: Component,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Return True if there is at least one association with the component."""
        assert component.id is not None
        params = (component.id,)
        return self._has_rows(
            self._HAS_ASSOCIATION_BY_COMPONENT_QUERY,
            params,
            connection=connection,
        )

    _HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_TYPE_QUERY = f"""
        SELECT attribute_id
        FROM {TABLE_NAME}
        WHERE component_id = ? AND attribute_type = ?
        LIMIT 1
    """

    def has_association_by_component_and_attribute_type(
        self,
        component: Component,
        attribute_type: str,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Return True if the component has an association with a supplemental attribute of the
        given type.
        """
        assert component.id is not None
        params = (component.id, attribute_type)
        return self._has_rows(
            self._HAS_ASSOCIATION_BY_COMPONENT_AND_ATTRIBUTE_TYPE_QUERY,
            params,
            connection=connection,
        )

    def _has_rows(
        self,
        query: str,
        params: Sequence[Any],
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        con = connection or self._con
        cur = con.cursor()
        res = execute(cur, query, params=params).fetchone()
        return res is not None

    _LIST_ASSOCIATED_COMPONENT_IDS_QUERY = f"""
        SELECT component_id
        FROM {TABLE_NAME}
        WHERE attribute_id = ?
    """

    def list_associated_component_ids(self, attribute: SupplementalAttribute) -> list[int]:
        """Return the component IDs associated with the attribute."""
        assert attribute.id is not None
        params = (attribute.id,)
        cur = self._con.cursor()
        rows = execute(cur, self._LIST_ASSOCIATED_COMPONENT_IDS_QUERY, params=params)
        return [x[0] for x in rows]

    def _build_associated_attribute_ids_query(self) -> str:
        """Return the base query for listing supplemental attribute IDs."""
        return f"""
            SELECT attribute_id
            FROM {TABLE_NAME}
        """

    def list_associated_supplemental_attribute_ids(
        self,
        component: Component,
        attribute_type: Optional[str] = None,
    ) -> list[int]:
        """Return the supplemental attribute IDs associated with the component and attribute type."""
        assert component.id is not None
        base = self._build_associated_attribute_ids_query()
        if attribute_type is None:
            query = f"{base} WHERE component_id = ?"
            params = (component.id,)
        else:
            query = f"{base} WHERE attribute_type = ? AND component_id = ?"
            params = (attribute_type, component.id)
        cur = self._con.cursor()
        rows = execute(cur, query, params=params)
        return [x[0] for x in rows]

    def remove_association_by_attribute(
        self,
        attribute: SupplementalAttribute,
        must_exist: bool = True,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Remove all associations with the given attribute."""
        assert attribute.id is not None
        where_clause = "WHERE attribute_id = ?"
        params = (attribute.id,)
        num_deleted = self._remove_associations(where_clause, params, connection=connection)
        if must_exist and num_deleted < 1:
            msg = f"Bug: unexpected number of deletions: {num_deleted}. Should have been >= 1."
            raise Exception(msg)

    def remove_association(
        self,
        component: Component,
        attribute: SupplementalAttribute,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Remove the association between the attribute and component."""
        assert attribute.id is not None
        assert component.id is not None
        where_clause = "WHERE attribute_id = ? AND component_id = ?"
        params = (attribute.id, component.id)
        num_deleted = self._remove_associations(where_clause, params, connection=connection)
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

    def _remove_associations(
        self,
        where_clause: str,
        params: Sequence[Any],
        connection: sqlite3.Connection | None = None,
    ) -> int:
        query = f"DELETE FROM {TABLE_NAME} {where_clause}"
        con = connection or self._con
        cur = con.cursor()
        execute(cur, query, params)
        rows = execute(cur, "SELECT CHANGES() AS changes").fetchall()
        assert len(rows) == 1, rows
        row = rows[0]
        logger.debug("Deleted {} rows from the supplemental attribute associations table", row[0])
        if connection is None:
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
            SELECT COUNT(DISTINCT attribute_id) AS count
            FROM {TABLE_NAME}
        """

    def get_num_attributes(self) -> int:
        """Return the number of supplemental attributes."""
        cur = self._con.cursor()
        return execute(cur, self._GET_NUM_ATTRIBUTES_QUERY).fetchone()[0]

    _GET_NUM_COMPONENTS_WITH_ATTRIBUTES_QUERY = f"""
        SELECT COUNT(DISTINCT component_id) AS count
        FROM {TABLE_NAME}
    """

    def get_num_components_with_attributes(self) -> int:
        """Return the number of components with supplemental attributes."""
        cur = self._con.cursor()
        return execute(cur, self._GET_NUM_COMPONENTS_WITH_ATTRIBUTES_QUERY).fetchone()[0]

    def migrate_legacy_uuid_table(
        self,
        components: Sequence[Component],
        attributes: Sequence[SupplementalAttribute],
    ) -> None:
        """Migrate an existing association table from UUID columns to integer ID columns."""
        columns = _get_table_columns(self._con, TABLE_NAME)
        if "attribute_id" in columns and "component_id" in columns:
            return
        if "attribute_uuid" not in columns or "component_uuid" not in columns:
            return

        component_ids = {str(component.uuid): component.id for component in components}
        attribute_ids = {str(attribute.uuid): attribute.id for attribute in attributes}
        if any(id_ is None for id_ in component_ids.values()):
            msg = "Cannot migrate supplemental attribute associations before component IDs exist"
            raise RuntimeError(msg)
        if any(id_ is None for id_ in attribute_ids.values()):
            msg = "Cannot migrate supplemental attribute associations before attribute IDs exist"
            raise RuntimeError(msg)

        cur = self._con.cursor()
        rows = execute(
            cur,
            f"""
            SELECT id, attribute_uuid, attribute_type, component_uuid, component_type
            FROM {TABLE_NAME}
            """,
        ).fetchall()
        execute(cur, f"ALTER TABLE {TABLE_NAME} RENAME TO {TABLE_NAME}_legacy_uuid")
        create_supplemental_attribute_associations_table(self._con, table_name=TABLE_NAME)

        migrated_rows = []
        for row in rows:
            row_id, attribute_uuid, attribute_type, component_uuid, component_type = row
            attribute_id = attribute_ids.get(attribute_uuid)
            component_id = component_ids.get(component_uuid)
            if attribute_id is None or component_id is None:
                msg = (
                    "Cannot migrate supplemental attribute association with missing "
                    f"{attribute_uuid=} or {component_uuid=}"
                )
                raise RuntimeError(msg)
            migrated_rows.append(
                (row_id, attribute_id, attribute_type, component_id, component_type)
            )
            self._insert_parent_ids(cur, component_id, attribute_id)

        if migrated_rows:
            cur.executemany(self._INSERT_ASSOCIATION_QUERY, migrated_rows)
        execute(cur, f"DROP TABLE {TABLE_NAME}_legacy_uuid")
        self._con.commit()

    @staticmethod
    def _insert_parent_ids(cur: sqlite3.Cursor, component_id: int, attribute_id: int) -> None:
        cur.execute("INSERT OR IGNORE INTO components(id) VALUES(?)", (component_id,))
        cur.execute(
            "INSERT OR IGNORE INTO supplemental_attributes(id) VALUES(?)",
            (attribute_id,),
        )


def _get_table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table_name})").fetchall()}
