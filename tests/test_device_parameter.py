import pytest
from pydantic import Field, ValidationError
from typing_extensions import Annotated

from infrasys.device_parameter import DeviceParameter


class EmissionsParameter(DeviceParameter):
    co2_rate: Annotated[
        float,
        Field(ge=0.0, description="CO2 emissions intensity in kg/MWh."),
    ]


def test_device_parameter_validation():
    parameter = EmissionsParameter(co2_rate=350.0)
    assert parameter.co2_rate == 350.0

    with pytest.raises(ValidationError):
        EmissionsParameter(co2_rate=-1.0)


def test_device_parameter_assignment_validation():
    parameter = EmissionsParameter(co2_rate=120.0)

    with pytest.raises(ValidationError):
        parameter.co2_rate = -5.0
