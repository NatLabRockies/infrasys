from infrasys import Component
from infrasys.cost_curves import (
    CostCurve,
    FuelCurve,
    OfferCurveCost,
    OperationalCost,
    ProductionVariableCostCurve,
    UnitSystem,
)
from infrasys.device_parameter import DeviceParameter
from infrasys.function_data import LinearFunctionData
from infrasys.value_curves import InputOutputCurve, LinearCurve

from .models.simple_system import SimpleSystem


class CurveComponent(Component):
    cost_curve: CostCurve


class NestedCostCurve(ProductionVariableCostCurve):
    variable: CostCurve | FuelCurve | None = None


class ComponentWithProductionCost(Component):
    cost: NestedCostCurve | None = None


def test_cost_curve():
    # Cost curve
    cost_curve = CostCurve(
        value_curve=InputOutputCurve(
            function_data=LinearFunctionData(proportional_term=1.0, constant_term=2.0)
        ),
        vom_cost=InputOutputCurve(
            function_data=LinearFunctionData(proportional_term=2.0, constant_term=1.0),
        ),
        power_units=UnitSystem.NATURAL_UNITS,
    )

    assert isinstance(cost_curve.value_curve.function_data, LinearFunctionData)
    assert cost_curve.value_curve.function_data.proportional_term == 1.0
    assert isinstance(cost_curve.vom_cost.function_data, LinearFunctionData)
    assert cost_curve.vom_cost.function_data.proportional_term == 2.0


def test_fuel_curve():
    # Fuel curve
    fuel_curve = FuelCurve(
        value_curve=InputOutputCurve(
            function_data=LinearFunctionData(proportional_term=1.0, constant_term=2.0)
        ),
        vom_cost=InputOutputCurve(
            function_data=LinearFunctionData(proportional_term=2.0, constant_term=1.0)
        ),
        fuel_cost=2.5,
        startup_fuel_offtake=LinearCurve(3.0),
        power_units=UnitSystem.NATURAL_UNITS,
    )

    assert isinstance(fuel_curve.value_curve.function_data, LinearFunctionData)
    assert fuel_curve.value_curve.function_data.proportional_term == 1.0
    assert isinstance(fuel_curve.vom_cost.function_data, LinearFunctionData)
    assert fuel_curve.vom_cost.function_data.proportional_term == 2.0
    assert isinstance(fuel_curve.startup_fuel_offtake.function_data, LinearFunctionData)
    assert fuel_curve.startup_fuel_offtake.function_data.proportional_term == 3.0
    assert fuel_curve.fuel_cost == 2.5


def test_operational_cost_hierarchy():
    assert issubclass(OperationalCost, DeviceParameter)
    assert issubclass(OfferCurveCost, OperationalCost)


def test_value_curve_custom_serialization():
    component = CurveComponent(
        name="test",
        cost_curve=CostCurve(
            value_curve=InputOutputCurve(
                function_data=LinearFunctionData(proportional_term=1.0, constant_term=2.0)
            ),
            vom_cost=InputOutputCurve(
                function_data=LinearFunctionData(proportional_term=2.0, constant_term=1.0)
            ),
            power_units=UnitSystem.NATURAL_UNITS,
        ),
    )

    model_dump = component.model_dump(mode="json")
    assert model_dump["cost_curve"]["value_curve"]["function_data"]["proportional_term"] == 1.0

    model_dump = component.model_dump(context={"magnitude_only": True})
    assert model_dump["cost_curve"]["value_curve"]["function_data"]["proportional_term"] == 1.0

    model_dump = component.model_dump(mode="json", context={"magnitude_only": True})
    assert model_dump["cost_curve"]["value_curve"]["function_data"]["proportional_term"] == 1.0


def test_nested_value_curve_serialization(tmp_path):
    system = SimpleSystem(auto_add_composed_components=True)
    gen_name = "thermal-gen"
    gen_with_operation_cost = ComponentWithProductionCost(
        name=gen_name,
        cost=NestedCostCurve(
            power_units=UnitSystem.NATURAL_UNITS,
            value_curve=InputOutputCurve(
                function_data=LinearFunctionData(proportional_term=0, constant_term=10)
            ),
        ),
    )

    # Test serialization
    system.add_component(gen_with_operation_cost)
    filename = tmp_path / "value_curve.json"
    system.to_json(filename, overwrite=True)

    # Test deserialization
    deserialized_system = SimpleSystem.from_json(filename)
    gen_deserialized = deserialized_system.get_component(ComponentWithProductionCost, gen_name)
    assert gen_deserialized is not None
    assert gen_deserialized.cost == gen_with_operation_cost.cost
