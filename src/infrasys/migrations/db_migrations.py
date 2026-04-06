import json
import sqlite3
import uuid
import warnings

from loguru import logger

from infrasys import (
    TIME_SERIES_ASSOCIATIONS_TABLE,
    TIME_SERIES_METADATA_TABLE,
)
from infrasys.time_series_metadata_store import make_features_string
from infrasys.utils.metadata_utils import (
    create_associations_table,
    create_key_value_store,
)
from infrasys.utils.sqlite import execute
from infrasys.utils.time_utils import str_timedelta_to_iso_8601

_LEGACY_METADATA_TABLE = "legacy_metadata_backup"


def metadata_store_needs_migration(conn: sqlite3.Connection, version: str | None = None) -> bool:
    """Check if the database schema requires migration to the new format.

    Parameters
    ----------
    conn : sqlite3.Connection
        An active SQLite database connection.

    Returns
    -------
    bool
        True if migration is required (new table does not exist), False otherwise.
    """
    cursor = conn.cursor()
    query = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1"
    cursor.execute(query, (TIME_SERIES_ASSOCIATIONS_TABLE,))
    return not cursor.fetchone() is not None


def migrate_legacy_metadata_store(conn: sqlite3.Connection) -> bool:
    """Migrate the database from the legacy schema to the new separated schema.

    Handles the transition from an older schema (where time series metadata and
    associations were likely combined) to a newer schema featuring separate
    `TIME_SERIES_ASSOCIATIONS_TABLE` and `KEY_VALUE_STORE_TABLE`.

    Parameters
    ----------
    conn : sqlite3.Connection
        An active SQLite database connection where the migration will be performed.

    Returns
    -------
    bool
        True if the migration was performed successfully.

    Notes
    -----
    The migration process involves these steps:
    1. Verify the existing `TIME_SERIES_METADATA_TABLE` matches the expected
       legacy column structure.
    2. Rename the legacy table to a temporary backup name.
    3. Create the new `KEY_VALUE_STORE_TABLE` and `TIME_SERIES_ASSOCIATIONS_TABLE`.
    4. Read data row-by-row from the backup table.
    5. Transform legacy data:
       - Extract `user_attributes` from `metadata` JSON, renaming to `features`.
       - Convert string timedelta `resolution` to ISO 8601 duration format.
       - Set default `owner_category` to "Component".
       - Set default empty JSON object for `serialization_info`.
    6. Insert transformed data into the new `TIME_SERIES_ASSOCIATIONS_TABLE`.
    7. Create required indexes on the new associations table.
    8. Drop the temporary backup table.
    9. Commit the transaction.

    Returns
    -------
    bool:
        True if migration was successful
    """
    logger.info("Migrating legacy metadata schema.")

    legacy_columns = [
        "id",
        "time_series_uuid",
        "time_series_type",
        "initial_time",
        "resolution",
        "variable_name",
        "component_uuid",
        "component_type",
        "user_attributes_hash",
        "metadata",
    ]
    cursor = conn.cursor()

    cursor.execute(f"SELECT * FROM {TIME_SERIES_METADATA_TABLE} LIMIT 1")
    columns = [desc[0] for desc in cursor.description]
    if not all(column in columns for column in legacy_columns):
        logger.error(f"Legacy schema does not match expected columns: {columns}")
        msg = "Bug: Legacy schema doesn't match expected structure"
        raise NotImplementedError(msg)

    logger.debug("Creating backup tables.")
    execute(
        cursor,
        f"ALTER TABLE {TIME_SERIES_METADATA_TABLE} RENAME TO {_LEGACY_METADATA_TABLE}",
    )

    logger.info("Creating new schema tables.")
    create_key_value_store(connection=conn)
    create_associations_table(connection=conn)

    logger.info("Migrating data from legacy schema.")
    cursor.execute(f"SELECT * FROM {_LEGACY_METADATA_TABLE}")
    rows = cursor.fetchall()

    sql_data_to_insert = []
    normalization_in_metadata = []
    for row in rows:
        (
            id_val,
            time_series_uuid,
            time_series_type,
            initial_timestamp,
            resolution,
            name,
            owner_uuid,
            owner_type,
            features_hash,
            metadata_json,
        ) = row

        metadata = json.loads(metadata_json)

        # Creating a flatten metadata from legacy schema.
        unit_metadata = metadata.pop("quantity_metadata")

        # Keep track if any metadata had normalization.
        if "normalization" in metadata and metadata["normalization"]:
            normalization_in_metadata.append(True)

        features_dict = {}
        if metadata.get("user_attributes"):  # We renamed user_attributes to features
            features_dict = metadata.pop("user_attributes")

        owner_category = "Component"  # Legacy system did not had any other category.
        length = metadata.get("length", 0)

        # Old resolution was in timedelta format.
        if resolution is not None:
            resolution = str_timedelta_to_iso_8601(resolution)

        # Fix for timestamp from: 2020-01-01 00:00 -> 2020-01-01T00:00
        if initial_timestamp is not None:
            initial_timestamp = initial_timestamp.replace(" ", "T")
        sql_data_to_insert.append(
            {
                "time_series_uuid": time_series_uuid,
                "time_series_type": time_series_type,
                "initial_timestamp": initial_timestamp,
                "resolution": resolution,
                "length": length,
                "name": name,
                "owner_uuid": owner_uuid,
                "owner_type": owner_type,
                "owner_category": owner_category,
                "features_json": make_features_string(features_dict),
                "units": json.dumps(unit_metadata),
                "metadata_uuid": str(uuid.uuid4()),  # metadata_uuid did not exist on tehe legacy
            }
        )

    # Raise warning for users that had normalization
    if any(normalization_in_metadata):
        msg = "Normalization of `TimeSeries` was deprecated from infrasys. "
        msg += "Upgrader will drop this fields."
        warnings.warn(msg)

    # Exit if there is no data to ingest.
    if not sql_data_to_insert:
        execute(cursor, f"DROP TABLE {_LEGACY_METADATA_TABLE}")
        conn.commit()
        logger.info("Schema migration completed.")
        return True

    # If we do have data, we insert it
    logger.info(
        f"Inserting {len(sql_data_to_insert)} records into {TIME_SERIES_ASSOCIATIONS_TABLE}."
    )
    cursor.executemany(
        f"""
        INSERT INTO `{TIME_SERIES_ASSOCIATIONS_TABLE}` (
            time_series_uuid, time_series_type, initial_timestamp, resolution,
            length, name, owner_uuid, owner_type, owner_category, features, units,
            metadata_uuid
        ) VALUES (
            :time_series_uuid, :time_series_type, :initial_timestamp, :resolution,
            :length, :name, :owner_uuid, :owner_type, :owner_category,
            :features_json, :units, :metadata_uuid
        )
        """,
        sql_data_to_insert,
    )

    # Dropping legacy table since it is no longer required.
    execute(cursor, f"DROP TABLE {_LEGACY_METADATA_TABLE}")
    conn.commit()
    logger.info("Schema migration completed.")
    return True
