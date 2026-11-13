"""Keep the new unit API's configuration explicit at call sites."""

from collections.abc import Callable
from inspect import Parameter, signature
from typing import get_type_hints

import pytest

from infrasys import units
from infrasys.units import Unit, UnitInput, UnitSpec, convert_to_internal, format_for_display


def test_conversion_helpers_are_exported_from_units_package() -> None:
    assert {"UnitInput", "convert_to_internal", "format_for_display"} <= set(units.__all__)


def test_convert_to_internal_declares_supported_input_types() -> None:
    annotations = get_type_hints(convert_to_internal, localns={"UnitSpec": UnitSpec})
    assert annotations["value"] == int | float | UnitInput


@pytest.mark.parametrize(
    ("function", "configuration"),
    [
        (convert_to_internal, {"spec", "base_value", "base_unit"}),
        (
            format_for_display,
            {"spec", "unit_system", "base_value", "base_unit", "system_base"},
        ),
        (UnitSpec, {"base"}),
        (Unit, {"base"}),
        (UnitSpec.convert_structured_value, {"info"}),
    ],
)
def test_unit_configuration_parameters_are_keyword_only(
    function: Callable[..., object], configuration: set[str]
) -> None:
    parameters = signature(function).parameters
    for name in configuration:
        assert parameters[name].kind is Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    ("function", "arguments"),
    [
        (convert_to_internal, (1.0, Unit("MW"))),
        (format_for_display, (1.0, Unit("MW"))),
        (UnitSpec, ("pu", "base_power")),
        (Unit, ("pu", "base_power")),
    ],
)
def test_unit_configuration_rejects_positional_arguments(
    function: Callable[..., object], arguments: tuple[object, ...]
) -> None:
    with pytest.raises(TypeError, match="positional"):
        function(*arguments)
