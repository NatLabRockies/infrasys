"""Cross-sectional readers over the time series store.

The rest of the time series API is series-oriented: it hands back one owner's whole
array. Simulations need the transpose --- every component's value at one timestamp, then
the next timestamp --- and cannot afford to hold every array in memory to get it. These
readers are built once against a filter and then driven forward through time, reading
only the values for the requested timestamp on each step.

Readers are snapshots of the matching associations at build time. Adding or removing
time series afterwards does not change what a live reader covers; build a new one.
"""

from datetime import datetime
from typing import Any

from infrasys.time_series_models import QuantityMetadata
from infrasys.utils.time_utils import as_naive_utc, as_utc


class TimeSeriesReader:
    """Reads the value of every matched ``SingleTimeSeries`` at one timestamp.

    All series covered by a reader share one grid (initial timestamp, resolution, and
    length); the store rejects the build otherwise.

    Examples
    --------
    >>> reader = system.build_time_series_reader(timedelta(hours=1), name="active_power")
    >>> for timestamp in reader.timestamps:
    ...     values = reader.read(timestamp)  # {component id: float}
    """

    def __init__(
        self,
        store: Any,
        reader: Any,
        group_component_ids: list[tuple[int, ...]],
        units: dict[int, QuantityMetadata | None],
    ) -> None:
        self._store = store
        self._reader = reader
        self._group_component_ids = group_component_ids
        self._component_ids = tuple(id_ for ids in group_component_ids for id_ in ids)
        self._units = units

    @property
    def component_ids(self) -> tuple[int, ...]:
        """Return the ids of every component covered by this reader."""
        return self._component_ids

    @property
    def units(self) -> dict[int, QuantityMetadata | None]:
        """Return each component's stored units, or None where the series is unitless.

        Values returned by :meth:`read` are raw magnitudes in these units. Attaching
        ``pint`` quantities per value would dominate the cost of a stepping loop, so the
        readers leave that to the caller.
        """
        return self._units

    @property
    def grid(self) -> dict[str, Any]:
        """Return the shared grid: initial timestamp, resolution, and length."""
        return self._reader.grid()

    @property
    def timestamps(self) -> list[datetime]:
        """Return every timestamp on the reader's grid, in order."""
        return [as_naive_utc(x) for x in self._reader.timestamps()]

    def read(self, when: datetime) -> dict[int, Any]:
        """Return ``{component id: value}`` for every covered series at ``when``.

        Raises
        ------
        InvalidParameterError
            Raised by the store if ``when`` is not on the reader's grid.
        """
        self._store.static_read(self._reader, as_utc(when))
        values: dict[int, Any] = {}
        for index, component_ids in enumerate(self._group_component_ids):
            group = self._reader.group_values(index)
            # tolist() is markedly faster than indexing numpy scalars, but it would turn a
            # non-scalar element into nested lists, so only flatten the scalar case.
            values.update(zip(component_ids, group.tolist() if group.ndim == 1 else list(group)))
        return values

    def read_columns(self, when: datetime) -> list[tuple[tuple[int, ...], Any]]:
        """Return ``(component ids, values array)`` per columnar group at ``when``.

        The escape hatch for loops where building a dict per step is measurable: the
        arrays come straight from the store with no per-value Python objects. Each array
        is shaped ``(num_components, *element_shape)`` and aligned to its id tuple.
        """
        self._store.static_read(self._reader, as_utc(when))
        return [
            (component_ids, self._reader.group_values(index))
            for index, component_ids in enumerate(self._group_component_ids)
        ]


class ForecastReader:
    """Reads the forecast window of every matched forecast at one timestamp.

    Components whose forecasts are backed by the same array collapse to a single *slot*.
    The store performs one physical read per slot rather than one per component, and
    :meth:`read` hands every component in a slot the same array object rather than a copy.
    :meth:`read_slots` exposes the deduplicated form directly.

    Examples
    --------
    >>> reader = system.build_forecast_reader(timedelta(hours=1), name="active_power")
    >>> for timestamp in reader.timestamps:
    ...     windows = reader.read(timestamp)  # {component id: ndarray of (horizon,)}
    """

    def __init__(
        self,
        store: Any,
        reader: Any,
        component_ids: tuple[int, ...],
        slots: tuple[int, ...],
        units: dict[int, QuantityMetadata | None],
    ) -> None:
        self._store = store
        self._reader = reader
        self._component_ids = component_ids
        self._slots = slots
        self._units = units
        # The first entry index for each slot; reading only these covers every window.
        representatives: dict[int, int] = {}
        for entry_index, slot in enumerate(slots):
            representatives.setdefault(slot, entry_index)
        self._representatives = representatives

    @property
    def component_ids(self) -> tuple[int, ...]:
        """Return the ids of every component covered by this reader."""
        return self._component_ids

    @property
    def units(self) -> dict[int, QuantityMetadata | None]:
        """Return each component's stored units, or None where the series is unitless."""
        return self._units

    @property
    def timeline(self) -> dict[str, Any]:
        """Return the window timeline: initial timestamp, resolution, interval, and count."""
        return self._reader.timeline()

    @property
    def timestamps(self) -> list[datetime]:
        """Return every window start timestamp, in order."""
        return [as_naive_utc(x) for x in self._reader.timestamps()]

    @property
    def num_slots(self) -> int:
        """Return the number of distinct windows, one physical read each per step.

        This is at most ``len(component_ids)``, and smaller whenever components share a
        backing array --- for example after :meth:`System.transform_single_time_series`,
        or wherever many components were given the same profile.
        """
        return self._reader.num_slots()

    @property
    def slots(self) -> dict[int, int]:
        """Return ``{component id: slot}``. Equal slots mean a shared window."""
        return dict(zip(self._component_ids, self._slots))

    def components_by_slot(self) -> dict[int, list[int]]:
        """Return ``{slot: [component ids]}`` for callers driving work per unique window."""
        grouped: dict[int, list[int]] = {}
        for component_id, slot in zip(self._component_ids, self._slots):
            grouped.setdefault(slot, []).append(component_id)
        return grouped

    def read(self, when: datetime) -> dict[int, Any]:
        """Return ``{component id: window array}`` for every covered forecast at ``when``.

        Components sharing a slot share one array object, so a shared profile is
        materialized once no matter how many components carry it. Treat the arrays as
        read-only for that reason.

        Raises
        ------
        InvalidParameterError
            Raised by the store if ``when`` is not on the reader's timeline.
        """
        windows = self.read_slots(when)
        return {
            component_id: windows[slot]
            for component_id, slot in zip(self._component_ids, self._slots)
        }

    def read_slots(self, when: datetime) -> dict[int, Any]:
        """Return ``{slot: window array}`` at ``when``, one entry per unique window."""
        self._store.forecast_read(self._reader, as_utc(when))
        return {
            slot: self._reader.entry_values(entry_index)
            for slot, entry_index in self._representatives.items()
        }
