"""Defines base class for device-attached parameters."""

from abc import ABC

from infrasys.models import InfraSysBaseModel


class DeviceParameter(InfraSysBaseModel, ABC):
    """Abstract supertype for device-attached parameter models."""
