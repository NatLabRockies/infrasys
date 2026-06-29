import sqlite3

from loguru import logger

from infrasys import (
    COMPONENT_ASSOCIATIONS_TABLE,
    SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE,
)
from infrasys.utils.sqlite import execute


def create_supplemental_attribute_associations_table(
    connection: sqlite3.Connection,
    table_name: str = SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE,
    with_index: bool = True,
) -> bool:
    """
    Create the supplemental attribute associations table schema.

    Parameters
    ----------
    connection : sqlite3.Connection
        SQLite connection to the metadata store database.
    table_name : str, optional
        Name of the table to create, by default ``supplemental_attribute_associations``.
    with_index : bool, default True
        Whether to create associated lookup indexes.

    Returns
    -------
    bool
        True if the table exists or was created successfully.
    """
    cur = connection.cursor()
    execute(cur, "PRAGMA foreign_keys = ON")
    execute(cur, "CREATE TABLE IF NOT EXISTS components(id INTEGER PRIMARY KEY)")
    execute(
        cur,
        "CREATE TABLE IF NOT EXISTS supplemental_attributes(id INTEGER PRIMARY KEY)",
    )
    schema = [
        "id INTEGER PRIMARY KEY",
        "attribute_id INTEGER NOT NULL",
        "attribute_type TEXT",
        "component_id INTEGER NOT NULL",
        "component_type TEXT",
        "FOREIGN KEY(attribute_id) REFERENCES supplemental_attributes(id) ON DELETE CASCADE",
        "FOREIGN KEY(component_id) REFERENCES components(id) ON DELETE CASCADE",
    ]
    schema_text = ",".join(schema)
    execute(cur, f"CREATE TABLE IF NOT EXISTS {table_name}({schema_text})")
    logger.debug("Created supplemental attribute associations table {}", table_name)
    if with_index:
        create_supplemental_attribute_association_indexes(connection, table_name)
    result = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    connection.commit()
    return bool(result)


def create_supplemental_attribute_association_indexes(
    connection: sqlite3.Connection,
    table_name: str = "supplemental_attribute_associations",
) -> None:
    """Create lookup indexes for the supplemental attribute associations table."""
    cur = connection.cursor()
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_attribute "
        f"ON {table_name} (attribute_id, component_id, component_type)",
    )
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_component "
        f"ON {table_name} (component_id, attribute_id, attribute_type)",
    )
    connection.commit()


def create_component_associations_table(
    connection: sqlite3.Connection,
    table_name: str = COMPONENT_ASSOCIATIONS_TABLE,
    with_index: bool = True,
) -> bool:
    """
    Create the component associations table schema.

    Parameters
    ----------
    connection : sqlite3.Connection
        SQLite connection to the metadata store database.
    table_name : str, optional
        Name of the table to create, by default ``COMPONENT_ASSOCIATIONS_TABLE``.
    with_index : bool, default True
        Whether to create lookup indexes for the table.

    Returns
    -------
    bool
        True if the table exists or was created successfully.
    """
    cur = connection.cursor()
    execute(cur, "PRAGMA foreign_keys = ON")
    execute(
        cur,
        "CREATE TABLE IF NOT EXISTS components(id INTEGER PRIMARY KEY)",
    )
    schema = [
        "id INTEGER PRIMARY KEY",
        "component_id INTEGER NOT NULL",
        "component_type TEXT",
        "attached_component_id INTEGER NOT NULL",
        "attached_component_type TEXT",
        "FOREIGN KEY(component_id) REFERENCES components(id) ON DELETE CASCADE",
        "FOREIGN KEY(attached_component_id) REFERENCES components(id) ON DELETE CASCADE",
    ]
    schema_text = ",".join(schema)
    execute(cur, f"CREATE TABLE IF NOT EXISTS {table_name}({schema_text})")
    logger.debug("Created component associations table {}", table_name)
    if with_index:
        create_component_association_indexes(connection, table_name)
    result = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    connection.commit()
    return bool(result)


def create_component_association_indexes(
    connection: sqlite3.Connection,
    table_name: str = COMPONENT_ASSOCIATIONS_TABLE,
) -> None:
    """Create lookup indexes for the component associations table."""
    cur = connection.cursor()
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_component ON {table_name} (component_id)",
    )
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_attached_component "
        f"ON {table_name} (attached_component_id)",
    )
    connection.commit()
    return
