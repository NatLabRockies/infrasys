"""Defines classes for value curves using cost functions"""

from typing import Generic, overload

import numpy as np
from pydantic import Field
from typing_extensions import Annotated, TypeVar

from infrasys.exceptions import ISOperationNotAllowed
from infrasys.function_data import (
    LinearFunctionData,
    PiecewiseLinearData,
    PiecewiseStepData,
    QuadraticFunctionData,
    XYCoords,
    running_sum,
)
from infrasys.models import InfraSysBaseModel


class ValueCurve(InfraSysBaseModel):
    input_at_zero: Annotated[
        float | None,
        Field(
            description="Optional, an explicit representation of the input value at zero output."
        ),
    ] = None


# Valid function data types for each value curve
InputOutputCurveTypes = TypeVar(
    "InputOutputCurveTypes", bound=LinearFunctionData | QuadraticFunctionData | PiecewiseLinearData
)
IncrementalCurveTypes = TypeVar(
    "IncrementalCurveTypes", bound=LinearFunctionData | PiecewiseStepData
)
AverageRateCurveTypes = TypeVar(
    "AverageRateCurveTypes", bound=LinearFunctionData | PiecewiseStepData
)


class InputOutputCurve(ValueCurve, Generic[InputOutputCurveTypes]):
    """Input-output curve relating production quality to cost.

    An input-output curve, directly relating the production quantity to the cost:

    .. math:: y = f(x).

    Can be used, for instance, in the representation of a Cost Curve where :math:`x` is MW and
    :math:`y` is currency/hr, or in the representation of a Fuel Curve where :math:`x` is MW and
    :math:`y` is fuel/hr.
    """

    function_data: Annotated[
        InputOutputCurveTypes,
        Field(description="The underlying `FunctionData` representation of this `ValueCurve`"),
    ]


class IncrementalCurve(ValueCurve, Generic[IncrementalCurveTypes]):
    """Incremental/marginal curve to relate production quantity to cost derivative.

    An incremental (or 'marginal') curve, relating the production quantity to the derivative of
    cost:

    ..math:: y = f'(x).

    Can be used, for instance, in the representation of a Cost Curve
    where :math:`x` is MW and :math:`y` is currency/MWh, or in the representation of a Fuel Curve
    where :math:`x` is MW and :math:`y` is fuel/MWh.
    """

    function_data: Annotated[
        IncrementalCurveTypes,
        Field(description="The underlying `FunctionData` representation of this `ValueCurve`"),
    ]
    initial_input: Annotated[
        float | None,
        Field(
            description="The value of f(x) at the least x for which the function is defined, or \
                the origin for functions with no left endpoint, used for conversion to `InputOutputCurve`"
        ),
    ]

    def to_input_output(self) -> InputOutputCurve:
        """Function to convert IncrementalCurve to InputOutputCurve

        Function takes an IncrementalCurve and converts it to a corresponding
        InputOutputCurve depending on the type of function_data. If the IncrementalCurve
        uses LinearFunctionData, the new InputOutputCurve is created linear or quadratic data
        that correspond to the integral of the original linear function. If the input uses
        PiecewiseStepData, the slopes of each segment are used to calculate the corresponding
        y values for each x value and used to construct PiecewiseLinearData for the InputOutputCurve.

        Parameters
        ----------
        data : IncrementalCurve
            Original IncrementalCurve for conversion.

        Returns
        ----------
        InputOutputCurve
            InputOutputCurve using either QuadraticFunctionData or PiecewiseStepData.
        """
        match self.function_data:
            case LinearFunctionData():
                p = self.function_data.proportional_term
                m = self.function_data.constant_term

                c = self.initial_input
                if c is None:
                    msg = "Cannot convert `IncrementalCurve` with undefined `initial_input`"
                    raise ISOperationNotAllowed(msg)

                if p == 0:
                    return InputOutputCurve(
                        function_data=LinearFunctionData(proportional_term=m, constant_term=c)
                    )
                else:
                    return InputOutputCurve(
                        function_data=QuadraticFunctionData(
                            quadratic_term=p / 2, proportional_term=m, constant_term=c
                        ),
                        input_at_zero=self.input_at_zero,
                    )
            case PiecewiseStepData():
                c = self.initial_input
                if c is None:
                    msg = "Cannot convert `IncrementalCurve` with undefined `initial_input`"
                    raise ISOperationNotAllowed(msg)

                points = running_sum(self.function_data)

                return InputOutputCurve(
                    function_data=PiecewiseLinearData(
                        points=[XYCoords(p.x, p.y + c) for p in points]
                    ),
                    input_at_zero=self.input_at_zero,
                )


class AverageRateCurve(ValueCurve, Generic[AverageRateCurveTypes]):
    """Average rate curve relating production quality to average cost rate.

    An average rate curve, relating the production quantity to the average cost rate from the
    origin:

    .. math:: y = f(x)/x.

    Can be used, for instance, in the representation of a
    Cost Curve where :math:`x` is MW and :math:`y` is currency/MWh, or in the representation of a
    Fuel Curve where :math:`x` is MW and :math:`y` is fuel/MWh. Typically calculated by dividing
    absolute values of cost rate or fuel input rate by absolute values of electric power.
    """

    function_data: Annotated[
        AverageRateCurveTypes,
        Field(
            description="The underlying `FunctionData` representation of this `ValueCurve`, or \
                only the oblique asymptote when using `LinearFunctionData`"
        ),
    ]
    initial_input: Annotated[
        float | None,
        Field(
            description="The value of f(x) at the least x for which the function is defined, or \
                the origin for functions with no left endpoint, used for conversion to `InputOutputCurve`"
        ),
    ]

    def to_input_output(
        self,
    ) -> InputOutputCurve[LinearFunctionData | QuadraticFunctionData | PiecewiseLinearData]:
        """Function to convert IncrementalCurve to InputOutputCurve

        Function takes an AverageRateCurve and converts it to a corresponding
        InputOutputCurve depending on the type of function_data. If the AverageRateCurve
        uses LinearFunctionData, the new InputOutputCurve is created with either linear or quadratic
        function data, depending on if the original function data is constant or linear. If the
        input uses PiecewiseStepData, new y-values are calculated for each x value such that `f(x) = x*y`
        and used to construct PiecewiseLinearData for the InputOutputCurve.

        Parameters
        ----------
        data : AverageRateCurve
            Original AverageRateCurve for conversion.

        Returns
        ----------
        InputOutputCurve
            InputOutputCurve using either QuadraticFunctionData or PiecewiseStepData.
        """
        match self.function_data:
            case LinearFunctionData():
                p = self.function_data.proportional_term
                m = self.function_data.constant_term

                c = self.initial_input
                if c is None:
                    msg = "Cannot convert `AverageRateCurve` with undefined `initial_input`"
                    raise ISOperationNotAllowed(msg)

                if p == 0:
                    return InputOutputCurve(
                        function_data=LinearFunctionData(proportional_term=m, constant_term=c)
                    )
                else:
                    return InputOutputCurve(
                        function_data=QuadraticFunctionData(
                            quadratic_term=p,
                            proportional_term=m,
                            constant_term=c,  # type: ignore
                        ),
                        input_at_zero=self.input_at_zero,
                    )
            case PiecewiseStepData():
                c = self.initial_input
                if c is None:
                    msg = "Cannot convert `AverageRateCurve` with undefined `initial_input`"
                    raise ISOperationNotAllowed(msg)

                xs = self.function_data.x_coords
                ys = np.multiply(xs[1:], self.function_data.y_coords).tolist()
                ys.insert(0, c)  # type:ignore

                return InputOutputCurve(
                    function_data=PiecewiseLinearData(
                        points=[XYCoords(x, y) for x, y in zip(xs, ys)]
                    ),
                    input_at_zero=self.input_at_zero,
                )
            case _:
                msg = "Function is not valid for the type of data provided."
                raise ISOperationNotAllowed(msg)


def LinearCurve(
    proportional_term: float = 0.0, constant_term: float = 0.0
) -> InputOutputCurve[LinearFunctionData]:
    """Creates a linear curve using the given proportional and constant terms.

    Returns an instance of `InputOutputCurve` with the specified linear function parameters.

    If no arguments are provided, both the `proportional_term` and `constant_term` default to 0.

    Parameters
    ----------
    proportional_term : float, optional
        The slope of the linear curve. Defaults to 0.0.
    constant_term : float, optional
        The y-intercept of the linear curve. Defaults to 0.0.

    Returns
    -------
    InputOutputCurve
        An instance of `InputOutputCurve` with a `LinearFunctionData` object based on the given parameters.

    Examples
    --------
    >>> LinearCurve()
    InputOutputCurve(function_data=LinearFunctionData(proportional_term=0.0, constant_term=0.0))

    >>> LinearCurve(10)
    InputOutputCurve(function_data=LinearFunctionData(proportional_term=10.0, constant_term=0.0))

    >>> LinearCurve(10, 20)
    InputOutputCurve(function_data=LinearFunctionData(proportional_term=10.0, constant_term=20.0))

    >>> LinearCurve(proportional_term=5.0, constant_term=15.0)
    InputOutputCurve(function_data=LinearFunctionData(proportional_term=5.0, constant_term=15.0))
    """
    return InputOutputCurve(
        function_data=LinearFunctionData(
            proportional_term=proportional_term, constant_term=constant_term
        )
    )


@overload
def PiecewiseIncrementalCurve(
    initial_input: float | None,
    x_coords: list[float],
    slopes: list[float],
    /,
) -> IncrementalCurve[PiecewiseStepData]: ...


@overload
def PiecewiseIncrementalCurve(
    input_at_zero: float | None,
    initial_input: float | None,
    x_coords: list[float],
    slopes: list[float],
    /,
) -> IncrementalCurve[PiecewiseStepData]: ...


def PiecewiseIncrementalCurve(*args) -> IncrementalCurve[PiecewiseStepData]:
    """Create a piecewise incremental curve with Sienna-compatible call patterns.

    Supported call forms
    --------------------
    PiecewiseIncrementalCurve(initial_input, x_coords, slopes)
    PiecewiseIncrementalCurve(input_at_zero, initial_input, x_coords, slopes)
    """

    if len(args) == 3:
        initial_input, x_coords, slopes = args
        input_at_zero = None
    elif len(args) == 4:
        input_at_zero, initial_input, x_coords, slopes = args
    else:
        msg = (
            "PiecewiseIncrementalCurve expects either "
            "(initial_input, x_coords, slopes) or "
            "(input_at_zero, initial_input, x_coords, slopes)."
        )
        raise TypeError(msg)

    return IncrementalCurve(
        function_data=PiecewiseStepData(x_coords=x_coords, y_coords=slopes),
        initial_input=initial_input,
        input_at_zero=input_at_zero,
    )
