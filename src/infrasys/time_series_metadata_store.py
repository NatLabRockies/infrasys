"""Stores time series metadata in a SQLite database."""

import itertools
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from uuid import UUID

import orjson

from infrasys.id_manager import IDManager
from infrasys.utils.sqlite import backup, execute

from . import (
    TIME_SERIES_ASSOCIATIONS_TABLE,
    Component,
)
from .exceptions import ISAlreadyAttached, ISNotStored, ISOperationNotAllowed
from .serialization import (
    SerializedTypeMetadata,
    deserialize_type,
    serialize_value,
)
from .supplemental_attribute_manager import SupplementalAttribute
from .time_series_models import (
    TimeSeriesMetadata,
)
from .utils.metadata_utils import (
    create_associations_table,
    create_key_value_store,
    get_horizon,
    get_initial_timestamp,
    get_interval,
    get_resolution,
    get_window_count,
)

_OPTIONAL_INSERT_COLUMNS = (
    "horizon",
    "interval",
    "window_count",
    "scaling_factor_multiplier",
)


class TimeSeriesMetadataStore:
    """Stores time series metadata in a SQLite database."""

    def __init__(self, con: sqlite3.Connection, initialize: bool = True):
        self._con = con
        if initialize:
            assert create_associations_table(connection=self._con)
            create_key_value_store(connection=self._con)
        self._cache_metadata: dict[UUID, TimeSeriesMetadata] = {}
        self._metadata_id_manager = IDManager(next_id=1)
        self._time_series_id_manager = IDManager(next_id=1)
        self._owner_id_manager = IDManager(next_id=1)

    def _load_metadata_into_memory(self):
        query = f"SELECT * FROM {TIME_SERIES_ASSOCIATIONS_TABLE}"
        cursor = self._con.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in rows]
        for row in rows:
            assert "features" in row, (
                f"Bug: Features missing from {TIME_SERIES_ASSOCIATIONS_TABLE} table."
            )
            metadata = _deserialize_time_series_metadata(row)
            self._cache_metadata[metadata.uuid] = metadata
        return

    def add(
        self,
        metadata: TimeSeriesMetadata,
        *owners: Component | SupplementalAttribute,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Add metadata to the store.

        Raises
        ------
        ISAlreadyAttached
            Raised if the time series metadata already stored.
        """
        where_clause, params = self._make_where_clause(
            owners,
            metadata.name,
            metadata.type,
            **metadata.features,
        )

        con = connection or self._con
        cur = con.cursor()
        query = f"SELECT 1 FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE {where_clause}"
        res = execute(cur, query, params=params).fetchone()
        if res:
            msg = f"Time series with {metadata=} is already stored."
            raise ISAlreadyAttached(msg)

        # Will probably need to refactor if we introduce more metadata classes.
        resolution = get_resolution(metadata)
        initial_time = get_initial_timestamp(metadata)
        horizon = get_horizon(metadata)
        interval = get_interval(metadata)
        window_count = get_window_count(metadata)

        units = None
        if metadata.units:
            units = orjson.dumps(serialize_value(metadata.units))

        if metadata.id is None:
            metadata.id = self._metadata_id_manager.get_next_id()
        else:
            self._metadata_id_manager.advance_past(metadata.id)
        if metadata.time_series_id is None:
            metadata.time_series_id = self._time_series_id_manager.get_next_id()
        else:
            self._time_series_id_manager.advance_past(metadata.time_series_id)

        rows = [
            {
                "time_series_id": metadata.time_series_id,
                "time_series_storage_key": str(metadata.time_series_uuid),
                "time_series_type": metadata.type,
                "initial_timestamp": initial_time,
                "resolution": resolution,
                "horizon": horizon,
                "interval": interval,
                "window_count": window_count,
                "length": metadata.length if hasattr(metadata, "length") else None,
                "name": metadata.name,
                "owner_id": owner.id,
                "owner_storage_key": str(owner.uuid),
                "owner_type": owner.__class__.__name__,
                "owner_category": _get_owner_category(owner),
                "features": make_features_string(metadata.features),
                "units": units,
                "metadata_id": metadata.id,
                "metadata_storage_key": str(metadata.uuid),
            }
            for owner in owners
        ]
        self._insert_rows(rows, cur)
        if connection is None:
            self._con.commit()

        self._cache_metadata[metadata.uuid] = metadata
        # else, commit/rollback will occur at a higer level.
        return

    def get_time_series_counts(self) -> "TimeSeriesCounts":
        """Return summary counts of components and time series."""
        query = f"""
            SELECT
                owner_type
                ,time_series_type
                ,initial_timestamp
                ,resolution
                ,count(*) AS count
            FROM {TIME_SERIES_ASSOCIATIONS_TABLE}
            GROUP BY
                owner_type
                ,time_series_type
                ,initial_timestamp
                ,resolution
            ORDER BY
                owner_type
                ,time_series_type
                ,initial_timestamp
                ,resolution
        """
        cur = self._con.cursor()
        rows = execute(cur, query).fetchall()
        time_series_type_count = {(x[0], x[1], x[2], x[3]): x[4] for x in rows}

        time_series_count = execute(
            cur, f"SELECT COUNT(DISTINCT time_series_id) from {TIME_SERIES_ASSOCIATIONS_TABLE}"
        ).fetchall()[0][0]

        return TimeSeriesCounts(
            time_series_count=time_series_count,
            time_series_type_count=time_series_type_count,
        )

    def get_metadata(
        self,
        owner: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: Optional[str] = None,
        **features,
    ) -> TimeSeriesMetadata:
        """Return the metadata matching the inputs.

        Raises
        ------
        ISOperationNotAllowed
            Raised if more than one metadata instance matches the inputs.
        """
        metadata_list = self.list_metadata(
            owner,
            name=name,
            time_series_type=time_series_type,
            **features,
        )
        if not metadata_list:
            msg = "No time series matching the inputs is stored"
            raise ISNotStored(msg)

        if len(metadata_list) > 1:
            msg = f"Found more than metadata matching inputs: {len(metadata_list)}"
            raise ISOperationNotAllowed(msg)

        return metadata_list[0]

    def has_time_series(self, time_series_uuid: UUID) -> bool:
        """Return True if there is time series matching the UUID."""
        cur = self._con.cursor()
        query = f"SELECT 1 FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE time_series_storage_key = ?"
        row = execute(cur, query, params=(str(time_series_uuid),)).fetchone()
        return row

    def has_time_series_metadata(
        self,
        owner: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> bool:
        """Return True if there is time series metadata matching the inputs."""
        where_clause, params = self._make_where_clause(
            (owner,), name, time_series_type, **features
        )
        query = f"SELECT 1 FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE {where_clause}"
        cur = self._con.cursor()
        res = execute(cur, query, params=params).fetchone()
        return bool(res)

    def list_existing_time_series(self, time_series_uuids: Iterable[UUID]) -> set[UUID]:
        """Return the UUIDs that are present in the database with at least one reference."""
        cur = self._con.cursor()
        params = tuple(str(x) for x in time_series_uuids)
        if not params:
            return set()
        uuids = ",".join(itertools.repeat("?", len(params)))
        query = f"SELECT DISTINCT time_series_storage_key FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE time_series_storage_key IN ({uuids})"
        rows = execute(cur, query, params=params).fetchall()
        return {UUID(x[0]) for x in rows}

    def list_existing_time_series_uuids(self) -> set[UUID]:
        """Return the UUIDs that are present."""
        cur = self._con.cursor()
        query = f"SELECT DISTINCT time_series_storage_key FROM {TIME_SERIES_ASSOCIATIONS_TABLE}"
        rows = execute(cur, query).fetchall()
        return {UUID(x[0]) for x in rows}

    def list_missing_time_series(self, time_series_uuids: Iterable[UUID]) -> set[UUID]:
        """Return the time_series_uuids that are no longer referenced by any owner."""
        existing_uuids = self.list_existing_time_series(time_series_uuids)
        return set(time_series_uuids) - existing_uuids

    def list_metadata(
        self,
        *owners: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: str | None = None,
        **features,
    ) -> list[TimeSeriesMetadata]:
        """Return a list of metadata that match the query."""
        metadata_uuids = self._get_metadata_uuids_by_filter(
            owners, name, time_series_type, **features
        )
        return [
            self._cache_metadata[uuid] for uuid in metadata_uuids if uuid in self._cache_metadata
        ]

    def list_metadata_with_time_series_uuid(
        self, time_series_uuid: UUID, limit: int | None = None
    ) -> list[TimeSeriesMetadata]:
        """Return metadata attached to the given time_series_uuid.

        Parameters
        ----------
        time_series_uuid
            The UUID of the time series.
        limit
            The maximum number of metadata to return. If None, all metadata are returned.
        """
        params = (str(time_series_uuid),)
        limit_str = "" if limit is None else f"LIMIT {limit}"
        # Use the denormalized view
        query = f"""
        SELECT
            metadata_storage_key
        FROM {TIME_SERIES_ASSOCIATIONS_TABLE}
        WHERE
            time_series_storage_key = ? {limit_str}
        """
        cur = self._con.cursor()
        rows = execute(cur, query, params=params).fetchall()
        return [
            self._cache_metadata[UUID(x[0])] for x in rows if UUID(x[0]) in self._cache_metadata
        ]

    def list_rows(
        self,
        *components: Component | SupplementalAttribute,
        name: Optional[str] = None,
        time_series_type: str | None = None,
        columns=None,
        **features,
    ) -> list[tuple]:
        """Return a list of rows that match the query."""
        where_clause, params = self._make_where_clause(
            components, name, time_series_type, **features
        )
        cols = "*" if columns is None else ",".join(columns)
        query = f"SELECT {cols} FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE {where_clause}"
        cur = self._con.cursor()
        rows = execute(cur, query, params=params).fetchall()
        return rows

    def remove(
        self,
        *owners: Component | SupplementalAttribute,
        name: str | None = None,
        time_series_type: str | None = None,
        connection: sqlite3.Connection | None = None,
        **features,
    ) -> list[TimeSeriesMetadata]:
        """Remove all matching rows and return the metadata."""
        con = connection or self._con
        cur = con.cursor()
        where_clause, params = self._make_where_clause(owners, name, time_series_type, **features)

        query = (
            f"SELECT metadata_storage_key FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE ({where_clause})"
        )
        rows = execute(cur, query, params=params).fetchall()
        matches = len(rows)
        if not matches:
            msg = "No metadata matching the inputs is stored"
            raise ISNotStored(msg)

        query = f"DELETE FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE ({where_clause})"
        execute(cur, query, params=params)
        if connection is None:
            con.commit()
        count_deleted = execute(cur, "SELECT changes()").fetchall()[0][0]
        if matches != count_deleted:
            msg = f"Bug: Unexpected length mismatch: {matches=} {count_deleted=}"
            raise Exception(msg)

        unique_metadata_uuids = {UUID(row[0]) for row in rows}
        result: list[TimeSeriesMetadata] = []
        for metadata_uuid in unique_metadata_uuids:
            query_count = (
                f"SELECT COUNT(*) FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE metadata_storage_key = ?"
            )
            count_association = execute(cur, query_count, params=[str(metadata_uuid)]).fetchone()[
                0
            ]
            if count_association == 0:
                result.append(self._cache_metadata.pop(metadata_uuid))
            else:
                result.append(self._cache_metadata[metadata_uuid])
        return result

    def remove_by_metadata(
        self,
        metadata: TimeSeriesMetadata,
        connection: sqlite3.Connection | None = None,
    ) -> TimeSeriesMetadata:
        """Remove all associations for a given metadata and return the metadata."""
        con = connection or self._con
        cur = con.cursor()

        query = f"DELETE FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE metadata_storage_key = ?"
        execute(cur, query, params=(str(metadata.uuid),))

        if connection is None:
            con.commit()

        if metadata.uuid in self._cache_metadata:
            return self._cache_metadata.pop(metadata.uuid)
        else:
            return metadata

    def sql(self, query: str, params: Sequence[str] = ()) -> list[tuple]:
        """Run a SQL query on the time series metadata table."""
        cur = self._con.cursor()
        return execute(cur, query, params=params).fetchall()

    def _insert_rows(self, rows: list[dict], cur: sqlite3.Cursor) -> None:
        query = f"""
        INSERT INTO {TIME_SERIES_ASSOCIATIONS_TABLE} (
            time_series_id, time_series_storage_key, time_series_type, initial_timestamp,
            resolution, horizon, interval, window_count, length, name, owner_id,
            owner_storage_key, owner_type, owner_category, features, units, metadata_id,
            metadata_storage_key
        ) VALUES (
            :time_series_id, :time_series_storage_key, :time_series_type,
            :initial_timestamp, :resolution, :horizon, :interval, :window_count,
            :length, :name, :owner_id, :owner_storage_key, :owner_type,
            :owner_category, :features, :units, :metadata_id, :metadata_storage_key
        )
        """
        rows = self._normalize_insert_rows(rows)
        self._insert_parent_ids(rows, cur)
        cur.executemany(query, rows)

    def _make_components_str(
        self, params: list[str], *owners: Component | SupplementalAttribute
    ) -> str:
        if not owners:
            msg = "At least one component must be passed."
            raise ISOperationNotAllowed(msg)

        or_clause = "OR ".join((itertools.repeat("owner_id = ? ", len(owners))))

        for owner in owners:
            assert owner.id is not None
            params.append(owner.id)

        return f"({or_clause})"

    def _make_where_clause(
        self,
        owners: tuple[Component | SupplementalAttribute, ...],
        name: str | None,
        time_series_type: str | None,
        **features: str,
    ) -> tuple[str, list[str]]:
        params: list[str] = []
        component_str = self._make_components_str(params, *owners)

        if name is None:
            var_str = ""
        else:
            var_str = "AND name = ?"
            params.append(name)

        if time_series_type is None:
            ts_str = ""
        else:
            ts_str = "AND time_series_type = ?"
            params.append(time_series_type)

        if features:
            feat_filter = _make_features_filter(features, params)
            feat_str = f"AND {feat_filter}"
        else:
            feat_str = ""

        return f"({component_str} {var_str} {ts_str}) {feat_str}", params

    def unique_uuids_by_type(self, time_series_type: str):
        query = f"SELECT DISTINCT time_series_storage_key from {TIME_SERIES_ASSOCIATIONS_TABLE} where time_series_type = ?"
        params = (time_series_type,)
        uuid_strings = self.sql(query, params)
        return [UUID(ustr[0]) for ustr in uuid_strings]

    def serialize(self, filename: Path | str) -> None:
        """Serialize SQLite to file."""
        with sqlite3.connect(filename) as dst_con:
            self._con.backup(dst_con)
            cur = dst_con.cursor()
            # Drop all index from the database that were created manually (sql not null)
            index_to_drop = execute(
                cur, "SELECT name FROM sqlite_master WHERE type ='index' AND sql IS NOT NULL"
            ).fetchall()
            for index in index_to_drop:
                execute(cur, f"DROP INDEX {index[0]}")
        dst_con.close()
        backup(self._con, filename)
        return

    def _get_metadata_uuids_by_filter(
        self,
        owners: tuple[Component | SupplementalAttribute, ...],
        name: Optional[str] = None,
        time_series_type: str | None = None,
        **features: Any,
    ) -> list[UUID]:
        """Get metadata UUIDs that match the filter criteria using progressive filtering."""
        cur = self._con.cursor()

        where_clause, params = self._make_where_clause(owners, name, time_series_type)
        features_str = make_features_string(features)
        if features_str:
            params.append(features_str)
        query = f"SELECT metadata_storage_key FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE {where_clause} AND features = ?"
        rows = execute(cur, query, params=params).fetchall()

        if rows:
            return [UUID(row[0]) for row in rows]

        where_clause, params = self._make_where_clause(owners, name, time_series_type, **features)
        query = f"SELECT metadata_storage_key FROM {TIME_SERIES_ASSOCIATIONS_TABLE} WHERE {where_clause}"
        rows = execute(cur, query, params=params).fetchall()
        return [UUID(row[0]) for row in rows]

    def migrate_legacy_uuid_table(self, owners: Sequence[Component | SupplementalAttribute]) -> None:
        """Migrate legacy UUID association rows to integer ID columns."""
        columns = _get_table_columns(self._con, TIME_SERIES_ASSOCIATIONS_TABLE)
        if {"time_series_id", "owner_id", "metadata_id"}.issubset(columns):
            if "owner_storage_key" in columns:
                self._remap_owner_ids_from_storage_keys(owners)
            return
        if not {"time_series_uuid", "owner_uuid", "metadata_uuid"}.issubset(columns):
            return

        owner_ids = {str(owner.uuid): owner.id for owner in owners}
        if any(id_ is None for id_ in owner_ids.values()):
            msg = "Cannot migrate time series associations before owner IDs exist"
            raise RuntimeError(msg)

        cur = self._con.cursor()
        rows = execute(cur, f"SELECT * FROM {TIME_SERIES_ASSOCIATIONS_TABLE}").fetchall()
        columns_order = [row[1] for row in cur.execute(
            f"PRAGMA table_info({TIME_SERIES_ASSOCIATIONS_TABLE})"
        ).fetchall()]
        row_dicts = [dict(zip(columns_order, row)) for row in rows]
        execute(
            cur,
            f"ALTER TABLE {TIME_SERIES_ASSOCIATIONS_TABLE} "
            f"RENAME TO {TIME_SERIES_ASSOCIATIONS_TABLE}_legacy_uuid",
        )
        create_associations_table(self._con, table_name=TIME_SERIES_ASSOCIATIONS_TABLE)

        time_series_ids: dict[str, int] = {}
        metadata_ids: dict[str, int] = {}
        migrated = []
        for row in row_dicts:
            time_series_key = row["time_series_uuid"]
            metadata_key = row["metadata_uuid"]
            time_series_id = time_series_ids.setdefault(
                time_series_key, self._time_series_id_manager.get_next_id()
            )
            metadata_id = metadata_ids.setdefault(
                metadata_key, self._metadata_id_manager.get_next_id()
            )
            owner_id = owner_ids.get(row["owner_uuid"])
            if owner_id is None:
                msg = f"Cannot migrate time series association for owner_uuid={row['owner_uuid']}"
                raise RuntimeError(msg)
            migrated.append(
                {
                    "time_series_id": time_series_id,
                    "time_series_storage_key": time_series_key,
                    "time_series_type": row["time_series_type"],
                    "initial_timestamp": row["initial_timestamp"],
                    "resolution": row["resolution"],
                    "horizon": row["horizon"],
                    "interval": row["interval"],
                    "window_count": row["window_count"],
                    "length": row["length"],
                    "name": row["name"],
                    "owner_id": owner_id,
                    "owner_storage_key": row["owner_uuid"],
                    "owner_type": row["owner_type"],
                    "owner_category": row["owner_category"],
                    "features": row["features"],
                    "units": row["units"],
                    "metadata_id": metadata_id,
                    "metadata_storage_key": metadata_key,
                }
            )
        if migrated:
            self._insert_rows(migrated, cur)
        execute(cur, f"DROP TABLE {TIME_SERIES_ASSOCIATIONS_TABLE}_legacy_uuid")
        self._con.commit()
        self._cache_metadata.clear()
        self._load_metadata_into_memory()

    def _remap_owner_ids_from_storage_keys(
        self, owners: Sequence[Component | SupplementalAttribute]
    ) -> None:
        owner_ids = {str(owner.uuid): owner.id for owner in owners}
        cur = self._con.cursor()
        rows = execute(
            cur,
            f"""
            SELECT DISTINCT owner_storage_key
            FROM {TIME_SERIES_ASSOCIATIONS_TABLE}
            WHERE owner_storage_key IS NOT NULL
            """,
        ).fetchall()
        for (owner_storage_key,) in rows:
            owner_id = owner_ids.get(owner_storage_key)
            if owner_id is None:
                continue
            cur.execute("INSERT OR IGNORE INTO owners(id) VALUES(?)", (owner_id,))
            execute(
                cur,
                f"""
                UPDATE {TIME_SERIES_ASSOCIATIONS_TABLE}
                SET owner_id = ?
                WHERE owner_storage_key = ?
                """,
                (owner_id, owner_storage_key),
            )
        self._con.commit()

    @staticmethod
    def _insert_parent_ids(rows: list[dict], cur: sqlite3.Cursor) -> None:
        cur.executemany(
            "INSERT OR IGNORE INTO time_series(id) VALUES(?)",
            {(row["time_series_id"],) for row in rows},
        )
        cur.executemany(
            "INSERT OR IGNORE INTO owners(id) VALUES(?)",
            {(row["owner_id"],) for row in rows},
        )
        cur.executemany(
            "INSERT OR IGNORE INTO time_series_metadata(id) VALUES(?)",
            {(row["metadata_id"],) for row in rows},
        )

    def _normalize_insert_rows(self, rows: list[dict]) -> list[dict]:
        normalized = []
        owner_ids_by_storage_key: dict[str, int] = {}
        time_series_ids_by_storage_key: dict[str, int] = {}
        metadata_ids_by_storage_key: dict[str, int] = {}
        for row in rows:
            row = dict(row)
            for column in _OPTIONAL_INSERT_COLUMNS:
                row.setdefault(column, None)
            if "time_series_storage_key" not in row:
                row["time_series_storage_key"] = row.pop("time_series_uuid")
            if "time_series_id" not in row:
                row["time_series_id"] = self._get_or_create_id(
                    time_series_ids_by_storage_key,
                    row["time_series_storage_key"],
                    self._time_series_id_manager,
                )
            if "metadata_storage_key" not in row:
                row["metadata_storage_key"] = row.pop("metadata_uuid")
            if "metadata_id" not in row:
                row["metadata_id"] = self._get_or_create_id(
                    metadata_ids_by_storage_key,
                    row["metadata_storage_key"],
                    self._metadata_id_manager,
                )
            if "owner_storage_key" not in row:
                row["owner_storage_key"] = row.get("owner_uuid")
            if "owner_id" not in row:
                row["owner_id"] = self._get_or_create_id(
                    owner_ids_by_storage_key,
                    row.get("owner_storage_key") or "",
                    self._owner_id_manager,
                )
            row.pop("owner_uuid", None)
            normalized.append(row)
        return normalized

    @staticmethod
    def _get_or_create_id(
        ids_by_key: dict[str, int],
        key: str,
        id_manager: IDManager | None = None,
    ) -> int:
        if key not in ids_by_key:
            ids_by_key[key] = (
                id_manager.get_next_id() if id_manager is not None else len(ids_by_key) + 1
            )
        return ids_by_key[key]


@dataclass
class TimeSeriesCounts:
    """Summarizes the counts of time series by component type."""

    time_series_count: int
    # Keys are component_type, time_series_type, initial_time, resolution
    time_series_type_count: dict[tuple[str, str, str, str], int]


def _make_features_filter(features: dict[str, Any], params: list[str]) -> str:
    conditions = []
    for key, value in features.items():
        conditions.append("features LIKE ?")
        if isinstance(value, str):
            params.append(f'%"{key}":"{value}"%')
        elif isinstance(value, bool):
            params.append(f'%"{key}":{str(value).lower()}%')
        else:
            params.append(f'%"{key}":{value}%')
    return " AND ".join(conditions)


def _make_features_dict(features: dict[str, Any]) -> dict[str, Any]:
    return {k: features[k] for k in sorted(features)}


def _deserialize_time_series_metadata(data: dict) -> TimeSeriesMetadata:
    """Deserialize a time series metadata dict into a typed metadata object.

    Works on a shallow copy of the input dict to avoid mutating the caller's data.
    """
    data = dict(data)
    data.pop("id", None)
    time_series_type = data.pop("time_series_type")
    # NOTE: This is only relevant for compatibility with IS.jl and can be
    # removed in the future when we have tigther integration
    if time_series_type == "DeterministicSingleTimeSeries":
        time_series_type = "Deterministic"

    serialized_type = SerializedTypeMetadata.validate_python(
        {
            "module": "infrasys",
            "type": time_series_type,
            "serialized_type": "base",
        }
    )
    metadata = deserialize_type(serialized_type).get_time_series_metadata_type()

    # Deserialize JSON columns
    for column in ["features", "scaling_factor_multiplier", "units"]:
        if data.get(column):
            data[column] = json.loads(data[column])

    # Features requires special handling since it is a sorted array with key value pairs.
    if data.get("features"):
        data["features"] = {k: v for d in data["features"] for k, v in d.items()}
    else:
        data["features"] = {}

    if "metadata_storage_key" in data:
        data["id"] = data.pop("metadata_id")
        data["legacy_uuid"] = data.pop("metadata_storage_key")
    else:
        data["legacy_uuid"] = data.pop("metadata_uuid")
    if "time_series_storage_key" in data:
        data["time_series_uuid"] = data.pop("time_series_storage_key")
    data["type"] = time_series_type
    metadata_instance = metadata.model_validate(
        {key: value for key, value in data.items() if key in metadata.model_fields}
    )
    return metadata_instance


def make_features_string(features: dict[str, Any]) -> str:
    """Serializes a dictionary of features into a sorted string."""
    data = [{key: value} for key, value in sorted(features.items())]
    return orjson.dumps(data).decode()


def _get_owner_category(owner: Component | SupplementalAttribute) -> str:
    return "Component" if isinstance(owner, Component) else "SupplementalAttribute"


def _get_table_columns(con: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table_name})").fetchall()}
