"""Defines models for time series arrays."""

import abc
import importlib
from datetime import datetime, timedelta
from enum import StrEnum
from typing import (
    Any,
    Sequence,
    Type,
    TypeAlias,
)

import numpy as np
import pandas as pd
import pint
from numpy.typing import NDArray
from pydantic import (
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
from infrasys.models import InfraSysBaseModel
from infrasys.normalization import NormalizationModel

TIME_COLUMN = "timestamp"
VALUE_COLUMN = "value"


ISArray: TypeAlias = Sequence | NDArray | pint.Quantity


class TimeSeriesStorageType(StrEnum):
    """Defines the possible storage types for time series."""

    TIME_SERIES_STORE = "time_series_store"


class TimeSeriesData(InfraSysBaseModel, abc.ABC):
    """Base class for all time series models.

    Time series identity is owned by the Rust ``infrastore`` core (content hash plus
    the owner/name/features association key); infrasys does not assign its own id/uuid.
    """

    name: str
    normalization: NormalizationModel = None

    @property
    def summary(self) -> str:
        """Return the name of the time series array with its type."""
        return f"{self.__class__.__name__}.{self.name}"


class SingleTimeSeries(TimeSeriesData):
    """Defines a time array with a single dimension of floats."""

    data: NDArray | pint.Quantity
    resolution: timedelta
    initial_timestamp: datetime

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
        resolution: timedelta,
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
            Resolution of the time series (e.g., 30min, 1hr)
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
        time_index: Sequence[datetime],
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
        The current implementation only uses the time_index to infer the initial time and resolution.

        """
        # Infer initial time from the time_index.
        initial_timestamp = time_index[0]

        # This does not cover changes mult-resolution time index.
        resolution = time_index[1] - time_index[0]

        return SingleTimeSeries.from_array(
            data,
            name,
            initial_timestamp,
            resolution,
            normalization=normalization,
        )

    def make_timestamps(self) -> NDArray:
        """Return the timestamps as a numpy array."""
        return pd.date_range(
            start=self.initial_timestamp, periods=len(self.data), freq=self.resolution
        ).values

    @property
    def data_array(self) -> NDArray:
        if isinstance(self.data, pint.Quantity):
            return self.data.magnitude
        return self.data


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

    @property
    def data_array(self) -> NDArray:
        if isinstance(self.data, pint.Quantity):
            return self.data.magnitude
        return self.data

    @property
    def length(self) -> int:
        """Return the number of forecast windows."""
        return self.window_count


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
        The time interval between consecutive forecast windows. A single-window forecast
        (``window_count=1``) has no second window to step to, so ``timedelta(0)`` is the
        natural value there and is stored and returned verbatim.
    window_count : int
        The number of forecast windows.

    Attributes
    ----------
    data_array : NDArray
        Returns the underlying numpy array (stripping any Pint units if present).

    See Also
    --------
    infrasys.system.System.transform_single_time_series : Derive forecasts from stored
        ``SingleTimeSeries`` ("perfect forecast" scenarios).
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


def single_time_series_range(
    initial_timestamp: datetime,
    resolution: timedelta,
    length: int,
    start_time: datetime | None = None,
    slice_length: int | None = None,
) -> tuple[int, int]:
    """Return the ``(index, length)`` slice into a SingleTimeSeries array.

    Extracted from the former ``SingleTimeSeriesMetadata.get_range``; the infrastore
    backend uses it to translate a ``start_time``/``length`` request into array indices.
    """
    if start_time is None and slice_length is None:
        return (0, length)

    if start_time is None:
        index = 0
    else:
        if start_time < initial_timestamp:
            msg = f"{start_time=} is less than {initial_timestamp=}"
            raise ISConflictingArguments(msg)
        if start_time >= initial_timestamp + length * resolution:
            msg = f"{start_time=} is too large for {initial_timestamp=}, {length=}"
            raise ISConflictingArguments(msg)
        diff = start_time - initial_timestamp
        if (diff % resolution).total_seconds() != 0.0:
            msg = f"{start_time=} conflicts with {initial_timestamp=} and {resolution=}"
            raise ISConflictingArguments(msg)
        index = int(diff / resolution)
    if slice_length is None:
        slice_length = length - index

    if index + slice_length > length:
        msg = f"{start_time=} {slice_length=} conflicts with {length=}"
        raise ISConflictingArguments(msg)

    return (index, slice_length)


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


class TimeSeriesKey(InfraSysBaseModel):
    """Base class for time series keys."""

    name: str
    time_series_type: Type[TimeSeriesData]
    features: dict[str, Any] = {}


class SingleTimeSeriesKey(TimeSeriesKey):
    """Keys for SingleTimeSeries."""

    length: int
    initial_timestamp: datetime
    resolution: timedelta


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
