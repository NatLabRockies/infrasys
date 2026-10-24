# Unit-aware models

`infrasys.units` provides `HasUnits`, `HasPerUnit`, `Unit` (an alias for
`unit_spec`), `UnitSpec`, `UnitSystem`, `get_unit_system`, `set_unit_system`,
`unit_system`, and `ureg`. They are also available from `infrasys`.

## Why `Annotated`?

A `float` describes the stored numeric type; it does not remember units.
`Annotated[float, Unit(...)]` keeps that numeric type while attaching a unit and,
when needed, a base-field reference. `Annotated` itself performs no conversion.
Infrasys's `UnitSpec` supplies Pydantic validation hooks, and `HasUnits` collects
those specifications, binds referenced base units, and formats representations.
This lets calculations use ordinary numbers while the model class retains the
unit contract. Use `BaseQuantity` when the stored value should instead be a Pint
quantity carrying its own units.

Pydantic handles model validation and serialization. The `Unit` hook can convert
an absolute field even without `HasUnits`; relative natural-unit inputs need
`HasUnits` to resolve the base field's unit. Removing `HasUnits` from the model
below still accepts already-internal numbers, but its natural-unit mappings for
`power` and `ramp` fail because the base unit cannot be determined. Bare float
fields without `Unit` hooks do not convert unit mappings.

## Validation and ordinary serialization

This exact model accepts natural-unit mappings and stores normalized numbers:

```python
from typing import Annotated

from pydantic import BaseModel

from infrasys.units import HasUnits, Unit, UnitSystem, unit_system


class Device(HasUnits, BaseModel):
    base_power: Annotated[float, Unit("MW")]
    power: Annotated[float, Unit("pu", base="base_power")]
    ramp: Annotated[float, Unit("MW/min", base="base_power")]


device = Device.model_validate(
    {
        "base_power": 100,
        "power": {"value": 5000, "unit": "kW"},
        "ramp": {"value": 600, "unit": "MW/hour"},
    }
)
print(device.model_dump())
print(device.model_dump_json())
```

```text
{'base_power': 100.0, 'power': 0.05, 'ramp': 0.1}
{"base_power":100.0,"power":0.05,"ramp":0.1}
```

`base_power` is an absolute 100 MW reference. `power` converts 5000 kW to 5 MW,
then divides by 100 MW to store 0.05 per-unit. `ramp` converts 600 MW/hour to
10 MW/min, then divides by 100 MW to store 0.1 per minute (a per-unit rate).
The base metadata does not turn that rate into an ordinary dimensionless power.

Ordinary JSON carries neither these field-unit names nor their base references.
They remain in the class annotations, exposed through Pydantic field metadata:

```python
print(Device.model_fields["ramp"].metadata)
restored = Device.model_validate_json(device.model_dump_json())
assert restored.model_dump() == device.model_dump()
print(restored.model_dump_json())
```

```text
[UnitSpec(unit='MW/min', base='base_power')]
{"base_power":100.0,"power":0.05,"ramp":0.1}
```

This round trip works because the receiving code uses the same `Device` contract
and numeric inputs are already internal values. A generic JSON consumer cannot
infer those units from the numbers. Pydantic's generated JSON Schema describes
accepted numeric/envelope input, but does not export the declared unit names and
base relationships as dedicated schema properties.

## Display context is not serialization context

The supported `unit_system(...)` context controls representation only:

```python
with unit_system(UnitSystem.NATURAL_UNITS):
    print(repr(device))
    print(device.model_dump_json())
```

```text
Device(base_power='100.0 MW', power='5 MW', ramp='10 MW')
{"base_power":100.0,"power":0.05,"ramp":0.1}
```

The ramp display above is a current limitation: the formatter uses the base
field's `MW` label, although the physical ramp is 10 MW/min. Default display also
labels relative scalars `pu` without distinguishing rates. Neither display mode
changes the stored 0.1 or its rate contract. This model has no unit-aware JSON
context that switches these scalar dumps to natural-unit strings or envelopes;
`unit_system(...)` is not a Pydantic serialization option.

## Explicit configuration in the unit API

Low-level conversion keeps the input value positional and requires named
configuration: `convert_to_internal(value, *, spec, base_value=None, base_unit=None)`.
`format_for_display(value, *, spec, unit_system, base_value=None, base_unit=None,
system_base=None)` follows the same convention. Both live in
`infrasys.units.conversion`. `Unit(unit, *, base=None)` and
`UnitSpec(unit, *, base=None)` keep the unit name as the primary argument and the
base reference keyword-only.

## Other input and persistence contracts

The mixin precedes `BaseModel` or `Component` in the inheritance list. Numeric
inputs are already in internal units. A scalar `{value, unit}` mapping converts
compatible units; a field with a base divides by that base. Declare annotated
base fields before dependent fields. A zero, missing or nonfinite base fails
natural-input conversion. Rates preserve the declared residual time unit.

Structured model inputs with a base divide their numeric fields by that base.
For nested `HasUnits` models, only absolute unit fields are divided. Unannotated
numeric fields and nested per-unit fields are preserved. Structured dumps use
`{"value": { ... }, "unit": "pu"}` so revalidation does not divide twice.
This envelope is distinct from `BaseQuantity.to_dict()`'s `{value, units}` format.
Optional values remain `None`. Scalar dumps remain internal numbers.
Structured conversion accepts alias-only fields and keeps Pydantic validation
active when rebuilding converted values. Serialization exclusions do not remove
required inputs during conversion.

`ureg` is the existing `BaseQuantity` registry. The annotation API stores numbers;
`BaseQuantity` continues to store Pint quantities and retains its serialization
contract. Neither API silently replaces the other.

`unit_system(mode)` temporarily controls representation only. The display state
is process-global, as in the original implementation, and is not task-local.
`HasPerUnit.bind_system_base(value)` supplies instance-local display state.
Infrasys `System` does not select or bind a power-system base automatically.
Unit display honors `Field(repr=False)`. Structured values retain their native
model representation in every display mode; only scalar values receive numeric
unit formatting. The initial migration preserves the source formatting
limitations for compound rates.

The implementation and regression tests originate in r2x-core `feat/csv-2`
(PR #167) at `dd44fac8b231ab7bb1b96641b560e9617fca5725`, rather than R2X main.
At migration time, `feat/csv-2` was the only matching remote branch; an exact
lookup for `feat/csv` returned no branch. Infrasys does not import R2X.
R2X ingestion, parser policy, and automatic system-base binding remain in R2X.
