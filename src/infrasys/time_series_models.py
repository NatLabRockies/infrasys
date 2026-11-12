"""Defines models for time series arrays."""

import abc
import importlib
import sqlite3
from datetime import datetime, timedelta
from enum import StrEnum
from typing import (
    Any,
    Literal,
    Optional,
    Sequence,
    Type,
    TypeAlias,
    Union,
)
from uuid import UUID

import numpy as np
import pandas as pd
import pint
from dateutil.relativedelta import relativedelta
from numpy.typing import NDArray
from pydantic import (
    Field,
    WithJsonSchema,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated

from infrasys.exceptions import (
    ISConflictingArguments,
)
from infrasys.models import InfraSysBaseModel, InfraSysBaseModelWithIdentifers
from infrasys.normalization import NormalizationModel
from infrasys.utils.time_utils import from_iso_8601, to_iso_8601

TIME_COLUMN = "timestamp"
VALUE_COLUMN = "value"
MONTHS_PER_YEAR = 12


ISArray: TypeAlias = Sequence | NDArray | pint.Quantity
TimeSeriesResolution: TypeAlias = timedelta | relativedelta


def validate_time_series_resolution(value: Any) -> Any:
    """Parse and validate a single-series resolution value.

    Strings are parsed as ISO 8601 durations. Relative calendar resolutions
    must represent a positive number of whole months without fixed or absolute
    fields. Years are included in the total month count. Other values are
    returned for Pydantic to validate against the field type.

    Parameters
    ----------
    value : Any
        Candidate resolution value.

    Returns
    -------
    Any
        Parsed duration or unchanged value for subsequent Pydantic validation.

    Raises
    ------
    ValueError
        If an ISO duration is malformed or a relative calendar resolution is
        unsupported.
    """
    if isinstance(value, str):
        value = from_iso_8601(value)
    if isinstance(value, relativedelta):
        calendar_resolution = relativedelta(years=value.years, months=value.months)
        # Infrastore represents calendar resolutions as a count of months.
        total_months = MONTHS_PER_YEAR * value.years + value.months
        if total_months <= 0 or value != calendar_resolution:
            msg = "Calendar resolutions must be positive whole months or years only."
            raise ValueError(msg)
        return relativedelta(months=total_months)
    return value


class TimeSeriesStorageType(StrEnum):
    """Defines the possible storage types for time series."""

    MEMORY = "memory"
    ARROW = "arrow"
    CHRONIFY = "chronify"
    HDF5 = "hdf5"
    PARQUET = "parquet"


class TimeSeriesData(InfraSysBaseModelWithIdentifers, abc.ABC):
    """Base class for all time series models"""

    name: str
    normalization: NormalizationModel = None

    @property
    def summary(self) -> str:
        """Return the name of the time series array with its type."""
        return f"{self.__class__.__name__}.{self.name}"

    @staticmethod
    @abc.abstractmethod
    def get_time_series_metadata_type() -> Type["TimeSeriesMetadata"]:
        """Return the metadata type associated with this time series type."""


class SingleTimeSeries(TimeSeriesData):
    """Defines a time array with a single dimension of floats."""

    data: NDArray | pint.Quantity
    resolution: TimeSeriesResolution
    initial_timestamp: datetime

    @field_validator("resolution", mode="before")
    @classmethod
    def validate_resolution(cls, value: Any) -> TimeSeriesResolution:
        return validate_time_series_resolution(value)

    @field_serializer("resolution", when_used="json")
    def serialize_resolution(self, value: TimeSeriesResolution) -> str:
        return to_iso_8601(value)

    @computed_field  # type: ignore
    @property
    def length(self) -> int:
        """Return the length of the data."""
        return len(self.data)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, SingleTimeSeries):
            raise NotImplementedError
        is_equal = True
        for field in self.model_fields_set:
            if field == "data":
                if not (self.data == other.data).all():
                    is_equal = False
                    break
            else:
                if not getattr(self, field) == getattr(other, field):
                    is_equal = False
                    break
        return is_equal

    @field_validator("data", mode="before")
    @classmethod
    def check_data(cls, data) -> NDArray | pint.Quantity:  # Standarize what object we receive.
        """Check time series data."""
        if len(data) < 2:
            msg = f"SingleTimeSeries length must be at least 2: {len(data)}"
            raise ValueError(msg)

        if isinstance(data, pint.Quantity):
            if not isinstance(data.magnitude, np.ndarray):
                return type(data)(np.array(data.magnitude), units=data.units)
            return data

        if not isinstance(data, np.ndarray):
            return np.array(data)

        return data

    @classmethod
    def from_array(
        cls,
        data: ISArray,
        name: str,
        initial_timestamp: datetime,
        resolution: TimeSeriesResolution,
        normalization: NormalizationModel = None,
    ) -> "SingleTimeSeries":
        """Method of SingleTimeSeries that creates an instance from a sequence.

        Parameters
        ----------
        data
            Sequence that contains the values of the time series
        initial_time
            Start time for the time series (e.g., datetime(2020,1,1))
        resolution
            A fixed ``timedelta`` or a positive ``relativedelta`` containing
            only whole calendar months and years.
        name
            Name assigned to the values of the time series (e.g., active_power)

        Returns
        -------
        SingleTimeSeries

        See Also
        --------
        from_time_array:  Time index implementation

        Note
        ----
        - Length of the sequence is inferred from the data.
        """
        if normalization is not None:
            npa = data if isinstance(data, np.ndarray) else np.array(data)
            data = normalization.normalize_array(npa)

        return SingleTimeSeries(
            data=data,  # type: ignore
            name=name,
            initial_timestamp=initial_timestamp,
            resolution=resolution,
            normalization=normalization,
        )

    @classmethod
    def from_time_array(
        cls,
        data: ISArray,
        name: str,
        time_index: Sequence[datetime] | NDArray,
        normalization: NormalizationModel = None,
    ) -> "SingleTimeSeries":
        """Create SingleTimeSeries using time_index provided.

        Parameters
        ----------
        data
            Sequence that contains the values of the time series
        name
            Name assigned to the values of the time series (e.g., active_power)
        time_index
            Sequence that contains the index of the time series

        Returns
        -------
        SingleTimeSeries

        See Also
        --------
        from_array: Base implementation

        Note
        ----
        Three or more timestamps that match an anchored calendar-month/year grid infer a
        ``relativedelta``. With fewer timestamps, the resolution is inferred as a fixed
        ``timedelta`` because the cadence is ambiguous.

        """
        # Infer initial time from the time_index.
        initial_timestamp = pd.Timestamp(time_index[0]).to_pydatetime()

        # Multiresolution time indexes are not supported.
        resolution: TimeSeriesResolution | None = infer_calendar_resolution(time_index)
        if resolution is None:
            resolution = pd.Timestamp(time_index[1]).to_pydatetime() - initial_timestamp

        return SingleTimeSeries.from_array(
            data,
            name,
            initial_timestamp,
            resolution,
            normalization=normalization,
        )

    def make_timestamps(self) -> NDArray:
        """Return the timestamps as a numpy array."""
        if isinstance(self.resolution, relativedelta):
            timestamps = [
                self.initial_timestamp + index * self.resolution for index in range(len(self.data))
            ]
            return pd.DatetimeIndex(timestamps).values
        return pd.date_range(
            start=self.initial_timestamp, periods=len(self.data), freq=self.resolution
        ).values

    @staticmethod
    def get_time_series_metadata_type() -> Type["SingleTimeSeriesMetadata"]:
        return SingleTimeSeriesMetadata

    @property
    def data_array(self) -> NDArray:
        if isinstance(self.data, pint.Quantity):
            return self.data.magnitude
        return self.data


class SingleTimeSeriesScalingFactor(SingleTimeSeries):
    """Defines a time array with a single dimension of floats that are 0-1 scaling factors."""


def infer_calendar_resolution(
    time_index: Sequence[datetime] | NDArray,
    /,
) -> relativedelta | None:
    """Infer a calendar interval from at least three anchored timestamps.

    Parameters
    ----------
    time_index
        A sequence or one-dimensional array of timestamps.

    Returns
    -------
    relativedelta | None
        The whole-month or whole-year interval when all timestamps follow the same
        calendar grid, or ``None`` otherwise.

    Examples
    --------
    >>> from datetime import datetime
    >>> infer_calendar_resolution(
    ...     [datetime(2024, 1, 31), datetime(2024, 2, 29), datetime(2024, 3, 31)]
    ... )
    relativedelta(months=+1)
    """
    if len(time_index) < 3:
        return None
    try:
        timestamps = [pd.Timestamp(timestamp).to_pydatetime() for timestamp in time_index]
    except (TypeError, ValueError):
        return None
    if timestamps[1] <= timestamps[0]:
        return None

    resolution = relativedelta(timestamps[1], timestamps[0])
    calendar_resolution = relativedelta(years=resolution.years, months=resolution.months)
    if not (resolution.years or resolution.months) or resolution != calendar_resolution:
        return None
    if not all(
        timestamp == timestamps[0] + index * resolution
        for index, timestamp in enumerate(timestamps)
    ):
        return None
    validate_time_series_resolution(resolution)
    return resolution


class Forecast(TimeSeriesData):
    """Defines the time series types for forecast."""

    ...


class AbstractDeterministic(TimeSeriesData):
    """Defines the abstric type for deterministic time series forecast."""

    data: NDArray | pint.Quantity
    resolution: timedelta
    initial_timestamp: datetime
    horizon: timedelta
    interval: timedelta
    window_count: int

    @staticmethod
    def get_time_series_metadata_type() -> Type["DeterministicMetadata"]:
        return DeterministicMetadata

    @property
    def data_array(self) -> NDArray:
        if isinstance(self.data, pint.Quantity):
            return self.data.magnitude
        return self.data


class Deterministic(AbstractDeterministic):
    """A deterministic forecast for a particular data field in a Component.

    This is a Pydantic model used to represent deterministic forecasts where the forecast
    data is explicitly stored as a 2D array. Each row in the array represents a forecast window,
    and each column represents a time step within the forecast horizon.

    Parameters
    ----------
    data : NDArray | pint.Quantity
        The normalized forecast data as a 2D array.
    resolution : timedelta
        The resolution of the forecast time series.
    initial_timestamp : datetime
        The starting timestamp for the forecast.
    horizon : timedelta
        The forecast horizon, indicating the duration of each forecast window.
    interval : timedelta
        The time interval between consecutive forecast windows.
    window_count : int
        The number of forecast windows.

    Attributes
    ----------
    data_array : NDArray
        Returns the underlying numpy array (stripping any Pint units if present).

    See Also
    --------
    from_single_time_series : A classmethod that creates a deterministic forecast from
        an existing SingleTimeSeries for "perfect forecast" scenarios.
    """

    @classmethod
    def from_array(
        cls,
        data: ISArray,
        name: str,
        initial_timestamp: datetime,
        resolution: timedelta,
        horizon: timedelta,
        interval: timedelta,
        window_count: int,
    ) -> "Deterministic":
        """Constructor for `Deterministic` time series that creates an instance from a sequence.

        Parameters
        ----------
        data
            Sequence that contains the values of the time series
        name
            Name assigned to the values of the time series (e.g., active_power)
        initial_time
            Start time for the time series (e.g., datetime(2020,1,1))
        resolution
            Resolution of the time series (e.g., 30min, 1hr)
        horizon
            Horizon of the time series (e.g., 30min, 1hr)
        window_count
            Number of windows that the time series represent

        Returns
        -------
        Deterministic
        """

        return Deterministic(
            data=data,  # type: ignore
            name=name,
            initial_timestamp=initial_timestamp,
            resolution=resolution,
            horizon=horizon,
            interval=interval,
            window_count=window_count,
        )


DeterministicTimeSeriesType: TypeAlias = Deterministic


# TODO:
# read CSV and Parquet and convert each column to a SingleTimeSeries


class QuantityMetadata(InfraSysBaseModel):
    """Contains the metadata needed to de-serialize time series stored within a pint.Quantity."""

    module: str
    quantity_type: Annotated[Type, WithJsonSchema({"type": "string"})]
    units: str

    @field_serializer("quantity_type")
    def serialize_type(self, _):
        return self.quantity_type.__name__

    @model_validator(mode="before")
    @classmethod
    def deserialize_from_strings(cls, values: dict[str, Any]) -> dict[str, Any]:
        if isinstance(values["quantity_type"], str):
            module = importlib.import_module(values["module"])
            return {
                "module": values["module"],
                "quantity_type": getattr(module, values["quantity_type"]),
                "units": values["units"],
            }
        return values


class TimeSeriesMetadata(InfraSysBaseModelWithIdentifers, abc.ABC):
    """Defines common metadata for all time series."""

    name: str
    time_series_uuid: UUID
    features: dict[str, Any] = {}
    units: Optional[QuantityMetadata] = None
    normalization: NormalizationModel = None
    type: Literal[
        "SingleTimeSeries",
        "SingleTimeSeriesScalingFactor",
        "NonSequentialTimeSeries",
        "Deterministic",
    ]

    @property
    def label(self) -> str:
        """Return the name of the time series array with its type."""
        return f"{self.type}.{self.name}"

    @staticmethod
    @abc.abstractmethod
    def get_time_series_data_type() -> Type:
        """Return the data type associated with this metadata type."""
        pass

    @staticmethod
    @abc.abstractmethod
    def get_time_series_type_str() -> str:
        """Return the time series type as a string."""

    @classmethod
    def from_data(cls, time_series: Any, **features) -> Any:
        """Construct an instance of TimeSeriesMetadata."""


class SingleTimeSeriesMetadataBase(TimeSeriesMetadata, abc.ABC):
    """Base class for SingleTimeSeries metadata."""

    length: int
    initial_timestamp: datetime
    resolution: TimeSeriesResolution
    type: Literal["SingleTimeSeries", "SingleTimeSeriesScalingFactor"]

    @field_validator("resolution", mode="before")
    @classmethod
    def validate_resolution(cls, value: Any) -> TimeSeriesResolution:
        return validate_time_series_resolution(value)

    @field_serializer("resolution", when_used="json")
    def serialize_resolution(self, value: TimeSeriesResolution) -> str:
        return to_iso_8601(value)

    @classmethod
    def from_data(cls, time_series: SingleTimeSeries, **features) -> Any:
        """Construct a SingleTimeSeriesMetadata from a SingleTimeSeries."""
        units = (
            QuantityMetadata(
                module=type(time_series.data).__module__,
                quantity_type=type(time_series.data),
                units=str(time_series.data.units),
            )
            if isinstance(time_series.data, pint.Quantity)
            else None
        )
        return cls(
            name=time_series.name,
            resolution=time_series.resolution,
            initial_timestamp=time_series.initial_timestamp,
            length=time_series.length,  # type: ignore
            time_series_uuid=time_series.uuid,
            features=features,
            units=units,
            normalization=time_series.normalization,
            type=cls.get_time_series_type_str(),  # type: ignore
        )

    def get_range(
        self, start_time: datetime | None = None, length: int | None = None
    ) -> tuple[int, int]:
        """Return the range to be used to index into the dataframe."""
        if start_time is None and length is None:
            return (0, self.length)

        index = 0
        if start_time is not None:
            if start_time < self.initial_timestamp:
                msg = "{start_time=} is less than {self.initial_time=}"
                raise ISConflictingArguments(msg)
            if start_time >= self.initial_timestamp + self.length * self.resolution:
                msg = f"{start_time=} is too large: {self=}"
                raise ISConflictingArguments(msg)
            if isinstance(self.resolution, relativedelta):
                difference = relativedelta(start_time, self.initial_timestamp)
                interval = relativedelta(
                    self.initial_timestamp + self.resolution,
                    self.initial_timestamp,
                )
                elapsed_months = MONTHS_PER_YEAR * difference.years + difference.months
                step_months = MONTHS_PER_YEAR * interval.years + interval.months
                index, remainder = divmod(elapsed_months, step_months)
                if remainder or self.initial_timestamp + index * self.resolution != start_time:
                    msg = (
                        f"{start_time=} conflicts with initial_time={self.initial_timestamp} and "
                        f"resolution={self.resolution}"
                    )
                    raise ISConflictingArguments(msg)
            else:
                diff = start_time - self.initial_timestamp
                if (diff % self.resolution).total_seconds() != 0.0:
                    msg = (
                        f"{start_time=} conflicts with initial_time={self.initial_timestamp} and "
                        f"resolution={self.resolution}"
                    )
                    raise ISConflictingArguments(msg)
                index = int(diff / self.resolution)
        if length is None:
            length = self.length - index

        if index + length > self.length:
            msg = f"{start_time=} {length=} conflicts with {self=}"
            raise ISConflictingArguments(msg)

        validate_calendar_slice(self, start_time=start_time, index=index, length=length)
        return (index, length)

    @staticmethod
    def get_time_series_data_type() -> Type:
        return SingleTimeSeries


def validate_calendar_slice(
    metadata: SingleTimeSeriesMetadataBase,
    /,
    *,
    start_time: datetime | None,
    index: int,
    length: int,
) -> None:
    """Reject a calendar slice if re-anchoring would change its timestamps.

    The caller must first validate the slice index and length against the
    original series and ensure that ``start_time`` lies on its grid. Fixed
    resolutions and slices starting at the original anchor need no check.

    Parameters
    ----------
    metadata : SingleTimeSeriesMetadataBase
        Metadata containing the original timestamp and resolution.
    start_time : datetime | None
        Requested slice start, or ``None`` for the original start.
    index : int
        Zero-based index of the first requested timestamp.
    length : int
        Number of requested timestamps.

    Raises
    ------
    ISConflictingArguments
        If the slice cannot represent the original timestamps when re-anchored.
    """
    resolution = metadata.resolution
    if start_time is None or not isinstance(resolution, relativedelta):
        return

    initial_timestamp = metadata.initial_timestamp
    if start_time.day == initial_timestamp.day and start_time.tzinfo == initial_timestamp.tzinfo:
        return

    for offset in range(1, length):
        if start_time + offset * resolution != initial_timestamp + (index + offset) * resolution:
            msg = (
                f"Calendar slice at {start_time} cannot preserve the original timestamps. "
                "Read the full series and slice its data and timestamps instead."
            )
            raise ISConflictingArguments(msg)


class SingleTimeSeriesMetadata(SingleTimeSeriesMetadataBase):
    """Defines the metadata for a SingleTimeSeries."""

    type: Literal["SingleTimeSeries"] = "SingleTimeSeries"

    @staticmethod
    def get_time_series_type_str() -> str:
        return "SingleTimeSeries"


class SingleTimeSeriesScalingFactorMetadata(SingleTimeSeriesMetadataBase):
    """Defines the metadata for a SingleTimeSeriesScalingFactor."""

    type: Literal["SingleTimeSeriesScalingFactor"] = "SingleTimeSeriesScalingFactor"

    @staticmethod
    def get_time_series_type_str() -> str:
        return "SingleTimeSeriesScalingFactor"


class DeterministicMetadata(TimeSeriesMetadata):
    """Defines the metadata for Deterministic time series.

    This metadata can represent either:
    1. A regular Deterministic forecast with stored 2D data
    2. A DeterministicSingleTimeSeries that references a SingleTimeSeries (like Julia's approach)

    When the time_series_uuid points to a SingleTimeSeries (no separate Deterministic data file),
    the data is loaded on-the-fly from that SingleTimeSeries instead of from stored Deterministic data.
    This is detected by checking if the data file exists or by checking if there's a flag.
    """

    initial_timestamp: datetime
    resolution: timedelta
    interval: timedelta
    horizon: timedelta
    window_count: int
    type: Literal["Deterministic"]

    @staticmethod
    def get_time_series_data_type() -> Type[TimeSeriesData]:
        """Return the data type associated with this metadata type."""
        return Deterministic

    @staticmethod
    def get_time_series_type_str() -> str:
        """Return the time series type as a string."""
        return "Deterministic"

    @classmethod
    def from_data(
        cls, time_series: DeterministicTimeSeriesType, **features: Any
    ) -> "DeterministicMetadata":
        """Construct a DeterministicMetadata from a Deterministic time series."""
        units = (
            QuantityMetadata(
                module=type(time_series.data).__module__,
                quantity_type=type(time_series.data),
                units=str(time_series.data.units),
            )
            if isinstance(time_series.data, pint.Quantity)
            else None
        )

        return cls(
            name=time_series.name,
            initial_timestamp=time_series.initial_timestamp,
            resolution=time_series.resolution,
            interval=time_series.interval,
            horizon=time_series.horizon,
            window_count=time_series.window_count,
            time_series_uuid=time_series.uuid,
            features=features,
            units=units,
            normalization=time_series.normalization,
            type="Deterministic",
        )

    def get_range(
        self, start_time: datetime | None = None, length: int | None = None
    ) -> tuple[int, int]:
        """Return the range to be used to index into the dataframe."""
        horizon_steps = int(self.horizon / self.resolution)
        interval_steps = int(self.interval / self.resolution)
        total_steps = interval_steps * (self.window_count - 1) + horizon_steps

        if start_time is None and length is None:
            return (0, total_steps)

        if start_time is None:
            index = 0
        else:
            if start_time < self.initial_timestamp:
                msg = f"{start_time=} is less than {self.initial_timestamp=}"
                raise ISConflictingArguments(msg)

            last_valid_time = (
                self.initial_timestamp + (self.window_count - 1) * self.interval + self.horizon
            )
            if start_time > last_valid_time:
                msg = f"{start_time=} is too large: {self=}"
                raise ISConflictingArguments(msg)

            diff = start_time - self.initial_timestamp
            if (diff % self.resolution).total_seconds() != 0.0:
                msg = (
                    f"{start_time=} conflicts with initial_timestamp={self.initial_timestamp} and "
                    f"resolution={self.resolution}"
                )
                raise ISConflictingArguments(msg)

            index = int(diff / self.resolution)

        if length is None:
            length = total_steps - index

        if index + length > total_steps:
            msg = f"{start_time=} {length=} conflicts with {self=}"
            raise ISConflictingArguments(msg)

        return (index, length)

    @property
    def length(self) -> int:
        """Return the total length of the deterministic time series."""
        horizon_steps = int(self.horizon / self.resolution)
        interval_steps = int(self.interval / self.resolution)
        return interval_steps * (self.window_count - 1) + horizon_steps


TimeSeriesMetadataUnion = Annotated[
    Union[
        SingleTimeSeriesMetadata,
        SingleTimeSeriesScalingFactorMetadata,
        DeterministicMetadata,
    ],
    Field(discriminator="type"),
]


class NonSequentialTimeSeries(TimeSeriesData):
    """Defines a non-sequential time array with a single dimension of floats."""

    data: NDArray | pint.Quantity
    timestamps: NDArray

    @computed_field
    def length(self) -> int:
        """Return the length of the data."""
        return len(self.data)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, NonSequentialTimeSeries):
            raise NotImplementedError
        is_equal = True
        for field in self.model_fields_set:
            if field == "data":
                if not (self.data == other.data).all():
                    is_equal = False
                    break
            elif field == "timestamps":
                if not all(t1 == t2 for t1, t2 in zip(self.timestamps, other.timestamps)):
                    is_equal = False
                    break
            else:
                if not getattr(self, field) == getattr(other, field):
                    is_equal = False
                    break
        return is_equal

    @field_validator("data", mode="before")
    @classmethod
    def check_data(cls, data) -> NDArray | pint.Quantity:
        """Check time series data."""
        if len(data) < 2:
            msg = f"NonSequentialTimeSeries length must be at least 2: {len(data)}"
            raise ValueError(msg)

        if isinstance(data, pint.Quantity):
            if not isinstance(data.magnitude, np.ndarray):
                return type(data)(np.array(data.magnitude), units=data.units)
            return data

        if not isinstance(data, np.ndarray):
            return np.array(data)

        return data

    @field_validator("timestamps", mode="before")
    @classmethod
    def check_timestamp(cls, timestamps: Sequence[datetime] | NDArray) -> NDArray:
        """Check non-sequential timestamps."""
        if len(timestamps) < 2:
            msg = f"Time index must have at least 2 timestamps: {len(timestamps)}"
            raise ValueError(msg)

        if len(timestamps) != len(set(timestamps)):
            msg = "Duplicate timestamps found. Timestamps must be unique."
            raise ValueError(msg)

        time_array = np.array(timestamps, dtype="datetime64[ns]")
        if not np.all(np.diff(time_array) > np.timedelta64(0, "s")):
            msg = "Timestamps must be in chronological order."
            raise ValueError(msg)

        if not isinstance(timestamps, np.ndarray):
            return np.array(timestamps)

        return timestamps

    @classmethod
    def from_array(
        cls,
        data: ISArray,
        timestamps: Sequence[datetime] | NDArray,
        name: str,
        normalization: NormalizationModel = None,
    ) -> "NonSequentialTimeSeries":
        """Method of NonSequentialTimeSeries that creates an instance from an array and timestamps.

        Parameters
        ----------
        data
            Sequence that contains the values of the time series
        timestamps
            Sequence that contains the non-sequential timestamps
        name
            Name assigned to the values of the time series (e.g., active_power)
        normalization
            Normalization model to normalize the data

        Returns
        -------
        NonSequentialTimeSeries
        """
        if normalization is not None:
            npa = data if isinstance(data, np.ndarray) else np.asarray(data)
            data = normalization.normalize_array(npa)

        return NonSequentialTimeSeries(
            data=data,  # type: ignore
            timestamps=timestamps,  # type: ignore
            name=name,
            normalization=normalization,
        )

    @staticmethod
    def get_time_series_metadata_type() -> Type["NonSequentialTimeSeriesMetadata"]:
        "Get the metadata type of the NonSequentialTimeSeries"
        return NonSequentialTimeSeriesMetadata

    @property
    def data_array(self) -> NDArray:
        "Get the data array NonSequentialTimeSeries"
        if isinstance(self.data, pint.Quantity):
            return self.data.magnitude
        return self.data

    @property
    def timestamps_array(self) -> NDArray:
        "Get the timestamps array NonSequentialTimeSeries"
        return self.timestamps


class NonSequentialTimeSeriesMetadataBase(TimeSeriesMetadata, abc.ABC):
    """Base class for NonSequentialTimeSeries metadata."""

    length: int
    type: Literal["NonSequentialTimeSeries"]

    @classmethod
    def from_data(
        cls, time_series: NonSequentialTimeSeries, **features
    ) -> "NonSequentialTimeSeriesMetadataBase":
        """Construct a NonSequentialTimeSeriesMetadata from a NonSequentialTimeSeries."""
        units = (
            QuantityMetadata(
                module=type(time_series.data).__module__,
                quantity_type=type(time_series.data),
                units=str(time_series.data.units),
            )
            if isinstance(time_series.data, pint.Quantity)
            else None
        )
        return cls(
            name=time_series.name,
            length=time_series.length,  # type: ignore
            time_series_uuid=time_series.uuid,
            features=features,
            units=units,
            normalization=time_series.normalization,
            type=cls.get_time_series_type_str(),  # type: ignore
        )

    @staticmethod
    def get_time_series_data_type() -> Type:
        return NonSequentialTimeSeries


class NonSequentialTimeSeriesMetadata(NonSequentialTimeSeriesMetadataBase):
    """Defines the metadata for a NonSequentialTimeSeries."""

    type: Literal["NonSequentialTimeSeries"] = "NonSequentialTimeSeries"

    @staticmethod
    def get_time_series_type_str() -> str:
        return "NonSequentialTimeSeries"


class TimeSeriesKey(InfraSysBaseModel):
    """Base class for time series keys."""

    name: str
    time_series_type: Type[TimeSeriesData]
    features: dict[str, Any] = {}


class SingleTimeSeriesKey(TimeSeriesKey):
    """Keys for SingleTimeSeries."""

    length: int
    initial_timestamp: datetime
    resolution: TimeSeriesResolution

    @field_validator("resolution", mode="before")
    @classmethod
    def validate_resolution(cls, value: Any) -> TimeSeriesResolution:
        return validate_time_series_resolution(value)

    @field_serializer("resolution", when_used="json")
    def serialize_resolution(self, value: TimeSeriesResolution) -> str:
        return to_iso_8601(value)


class NonSequentialTimeSeriesKey(TimeSeriesKey):
    """Keys for SingleTimeSeries."""

    length: int


class DeterministicTimeSeriesKey(TimeSeriesKey):
    """Keys for Deterministic time series."""

    initial_timestamp: datetime
    resolution: timedelta
    interval: timedelta
    horizon: timedelta
    window_count: int


class TimeSeriesStorageContext(InfraSysBaseModel):
    """Stores connections to the metadata and data databases during transactions."""

    metadata_conn: sqlite3.Connection
    data_context: Any = None
