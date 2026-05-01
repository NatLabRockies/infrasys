import importlib.metadata as metadata

from loguru import logger

logger.disable("infrasys")

__version__ = metadata.metadata("infrasys")["Version"]
TS_METADATA_FORMAT_VERSION = "1.0.0"

TIME_SERIES_ASSOCIATIONS_TABLE = "time_series_associations"
TIME_SERIES_METADATA_TABLE = "time_series_metadata"
KEY_VALUE_STORE_TABLE = "key_value_store"
SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE = "supplemental_attribute_associations"
COMPONENT_ASSOCIATIONS_TABLE = "component_associations"
SUPPLEMENTAL_ATTRIBUTE_ASSOCIATIONS_TABLE = "supplemental_attribute_associations"

from .base_quantity import BaseQuantity
from .component import Component
from .device_parameter import DeviceParameter
from .location import GeographicInfo, Location
from .normalization import NormalizationModel
from .supplemental_attribute import SupplementalAttribute
from .system import System
from .time_series_models import (
    Deterministic,
    NonSequentialTimeSeries,
    SingleTimeSeries,
    SingleTimeSeriesKey,
    TimeSeriesKey,
    TimeSeriesStorageType,
)

__all__ = (
    "BaseQuantity",
    "Component",
    "Deterministic",
    "DeviceParameter",
    "GeographicInfo",
    "Location",
    "NonSequentialTimeSeries",
    "NormalizationModel",
    "SingleTimeSeries",
    "SingleTimeSeriesKey",
    "SupplementalAttribute",
    "System",
    "TimeSeriesKey",
    "TimeSeriesStorageType",
)
