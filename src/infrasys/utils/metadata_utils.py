import sqlite3
from functools import singledispatch

from loguru import logger

from infrasys import (
    COMPONENT_ASSOCIATIONS_TABLE,
    KEY_VALUE_STORE_TABLE,
    SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE,
    TIME_SERIES_ASSOCIATIONS_TABLE,
    TS_METADATA_FORMAT_VERSION,
)
from infrasys.time_series_models import (
    DeterministicMetadata,
    NonSequentialTimeSeriesMetadataBase,
    SingleTimeSeriesMetadataBase,
    TimeSeriesMetadata,
)
from infrasys.utils.sqlite import execute
from infrasys.utils.time_utils import to_iso_8601


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
    schema = [
        "id INTEGER PRIMARY KEY",
        "attribute_uuid TEXT",
        "attribute_type TEXT",
        "component_uuid TEXT",
        "component_type TEXT",
    ]
    schema_text = ",".join(schema)
    cur = connection.cursor()
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
        f"ON {table_name} (attribute_uuid, component_uuid, component_type)",
    )
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_component "
        f"ON {table_name} (component_uuid, attribute_uuid, attribute_type)",
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
    schema = [
        "id INTEGER PRIMARY KEY",
        "component_uuid TEXT",
        "component_type TEXT",
        "attached_component_uuid TEXT",
        "attached_component_type TEXT",
    ]
    schema_text = ",".join(schema)
    cur = connection.cursor()
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
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_component ON {table_name} (component_uuid)",
    )
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS {table_name}_by_attached_component "
        f"ON {table_name} (attached_component_uuid)",
    )
    connection.commit()
    return


def create_associations_table(
    connection: sqlite3.Connection,
    table_name=TIME_SERIES_ASSOCIATIONS_TABLE,
    with_index: bool = True,
) -> bool:
    """
    Create the time series associations table schema on a DB connection.

    Parameters
    ----------
    connection : sqlite3.Connection
        SQLite connection to the metadata store database.
    table_name : str, optional
        Name of the table to create, by default ``TIME_SERIES_ASSOCIATIONS_TABLE``.
    with_index : bool, default True
        Whether to create the supporting indexes for the associations table.

    Returns
    -------
    bool
        True if the table was created successfully.
    """
    schema = [
        "id INTEGER PRIMARY KEY",
        "time_series_uuid TEXT NOT NULL",
        "time_series_type TEXT NOT NULL",
        "initial_timestamp TEXT",
        "resolution TEXT NULL",
        "horizon TEXT",
        "interval TEXT",
        "window_count INTEGER",
        "length INTEGER",
        "name TEXT NOT NULL",
        "owner_uuid TEXT NOT NULL",
        "owner_type TEXT NOT NULL",
        "owner_category TEXT NOT NULL",
        "features TEXT NOT NULL",
        "scaling_factor_multiplier TEXT NULL",
        "metadata_uuid TEXT NOT NULL",
        "units TEXT NULL",
    ]
    schema_text = ",".join(schema)
    cur = connection.cursor()
    execute(cur, f"CREATE TABLE {table_name}({schema_text})")
    logger.debug("Created time series associations table")
    if with_index:
        create_indexes(connection, table_name)

    # Return true if the table creation was succesfull
    result = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()

    if not result:
        msg = "Could not create the associations table."
        raise RuntimeError(msg)

    connection.commit()
    return bool(result)


def create_key_value_store(
    connection: sqlite3.Connection, table_name=KEY_VALUE_STORE_TABLE
) -> None:
    """
    Ensure the metadata key/value store exists with the current format version.

    Parameters
    ----------
    connection : sqlite3.Connection
        SQLite connection to the metadata store database.
    table_name : str, optional
        Name of the table to create, by default ``KEY_VALUE_STORE_TABLE``.
    """
    schema = ["key TEXT PRIMARY KEY", "value JSON NOT NULL"]
    schema_text = ",".join(schema)
    cur = connection.cursor()
    execute(cur, f"CREATE TABLE IF NOT EXISTS {table_name}({schema_text})")

    rows = [("version", TS_METADATA_FORMAT_VERSION)]
    placeholder = ",".join(["?"] * len(rows[0]))
    query = f"INSERT OR REPLACE INTO {table_name}(key, value) VALUES({placeholder})"
    cur.executemany(query, rows)
    connection.commit()
    logger.debug("Created metadata table")
    return


def create_indexes(
    connection: sqlite3.Connection, table_name=TIME_SERIES_ASSOCIATIONS_TABLE
) -> None:
    # Index strategy:
    # 1. Optimize for these user queries with indexes:
    #    1a. all time series attached to one component
    #    1b. time series for one component + variable_name + type
    #    1c. time series for one component with all user attributes
    # 2. Optimize for checks at system.add_time_series. Use all fields.
    # 3. Optimize for returning all metadata for a time series UUID.
    logger.debug("Creating indexes on {}.", table_name)
    cur = connection.cursor()
    execute(
        cur,
        f"CREATE UNIQUE INDEX IF NOT EXISTS by_c_vn_tst_hash ON {table_name} "
        f"(owner_uuid, time_series_type, name, resolution, features)",
    )
    execute(
        cur,
        f"CREATE INDEX IF NOT EXISTS by_ts_uuid ON {table_name} (time_series_uuid)",
    )
    return


@singledispatch
def get_resolution(metadata: TimeSeriesMetadata) -> str | None:
    """Get formatted resolution from metadata or None if not available."""
    return None


@get_resolution.register
def _(metadata: SingleTimeSeriesMetadataBase) -> str:
    """Get resolution from SingleTimeSeriesMetadataBase."""
    return to_iso_8601(metadata.resolution)


@get_resolution.register
def _(metadata: DeterministicMetadata) -> str:
    """Get resolution from DeterministicMetadata."""
    return to_iso_8601(metadata.resolution)


@singledispatch
def get_initial_timestamp(metadata: TimeSeriesMetadata) -> str | None:
    """Get formatted initial_timestamp from metadata or None if not available."""
    return None


@get_initial_timestamp.register
def _(metadata: SingleTimeSeriesMetadataBase) -> str:
    """Get initial_timestamp from SingleTimeSeriesMetadataBase. Format for initial_timestamp is YYYY-MM-DDThh:mm:ss."""
    return metadata.initial_timestamp.isoformat(sep="T")


@get_initial_timestamp.register
def _(metadata: DeterministicMetadata) -> str:
    """Get initial_timestamp from DeterministicMetadata. Format for initial_timestamp is YYYY-MM-DDThh:mm:ss"""
    return metadata.initial_timestamp.isoformat(sep="T")


@singledispatch
def get_horizon(metadata: TimeSeriesMetadata) -> str | None:
    """Get formatted horizon from metadata or None if not available."""
    return None


@get_horizon.register
def _(metadata: DeterministicMetadata) -> str:
    """Get horizon from DeterministicMetadata."""
    return to_iso_8601(metadata.horizon)


@singledispatch
def get_interval(metadata: TimeSeriesMetadata) -> str | None:
    """Get formatted interval from metadata or None if not available."""
    return None


@get_interval.register
def _(metadata: DeterministicMetadata) -> str:
    """Get interval from DeterministicMetadata."""
    return to_iso_8601(metadata.interval)


@singledispatch
def get_window_count(metadata: TimeSeriesMetadata) -> int | None:
    """Get window_count from metadata or None if not available."""
    return None


@get_window_count.register
def _(metadata: DeterministicMetadata) -> int:
    """Get window_count from DeterministicMetadata."""
    return metadata.window_count


@singledispatch
def get_length(metadata: TimeSeriesMetadata) -> int | None:
    """Get length from metadata or None if not available."""
    return None


@get_length.register
def _(metadata: SingleTimeSeriesMetadataBase) -> int:
    """Get length from SingleTimeSeriesMetadataBase."""
    return metadata.length


@get_length.register
def _(metadata: NonSequentialTimeSeriesMetadataBase) -> int:
    """Get length from NonSequentialTimeSeriesMetadataBase."""
    return metadata.length
