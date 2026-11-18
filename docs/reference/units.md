# Unit-aware models

The unit API is re-exported from `infrasys`. See the
[units API reference](api/units.md).
The {py:func}`Unit <infrasys.units.Unit>` helper aliases
{py:func}`unit_spec <infrasys.units.unit_spec>`. The
{py:class}`HasUnits <infrasys.units.HasUnits>` and
{py:class}`HasPerUnit <infrasys.units.HasPerUnit>` mixins use
{py:class}`UnitSpec <infrasys.units.UnitSpec>` metadata.

## Why `Annotated`?

A bare {py:class}`float` field stores a number without unit metadata or automatic
unit conversion. Use it for dimensionless values or when the surrounding
application manages units. {py:class}`typing.Annotated` keeps the stored value
numeric while attaching a unit declaration. It does not convert values itself.
Pydantic applies the validation hooks from
{py:class}`~infrasys.units.UnitSpec`, while
{py:class}`~infrasys.units.HasUnits` resolves base references and adds unit
information to model representations.

### Choosing a field declaration

- Plain number. A bare {py:class}`float` has no unit contract. It does not
  parse unit mappings or convert units.
- Absolute unit, no base. Use the {py:func}`~infrasys.units.Unit` helper,
  for example `Unit("MW")`, when values should be stored as numeric magnitudes
  in a fixed physical unit. Numeric inputs are assumed to use that unit.
  Compatible `{value, unit}` mappings are converted into it. The
  {py:class}`~infrasys.units.HasUnits` mixin is optional for conversion and is
  needed only when unit labels should appear in `repr()`.
- Relative to a model base. Use the same {py:func}`~infrasys.units.Unit`
  helper with `base=...`, such as `Unit("pu", base="base_power")`, when values
  should be stored relative to a model field. The
  {py:class}`~infrasys.units.HasUnits` mixin resolves that field's unit and
  instance value to convert physical-unit mappings. Numeric inputs are treated
  as already expressed relative to the base.
- Pint quantity. Use a domain-specific subclass of
  {py:class}`~infrasys.base_quantity.BaseQuantity` when the value itself should
  carry units and support Pint's unit-aware arithmetic. This stores a Pint
  quantity rather than an ordinary float.

A {py:func}`~infrasys.units.Unit` declaration with `unit="pu"` and no base
describes values already expressed per-unit. A physical-unit mapping needs a
base reference to convert. With a base reference, the declared unit can also be
a physical unit, for example `Unit("MW/min", base="base_power")`; the stored
number is relative to the base and retains its per-minute rate meaning. A
{py:class}`~infrasys.units.UnitSpec` contains the base-field name, not the
instance's base value. The same specification can be used by models with
different base values.

An absolute {py:func}`~infrasys.units.Unit` field can convert mappings without
{py:class}`~infrasys.units.HasUnits`. A base-relative field needs the mixin to
resolve its base unit and value. Without it, pre-normalized numeric values are
still accepted, but physical-unit mappings cannot be converted. Bare float
fields do not convert unit mappings.

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
accepted numbers and unit mappings, but does not export the declared unit names
and base relationships as dedicated schema properties.

## Choosing a display mode

{py:class}`UnitSystem <infrasys.units.UnitSystem>` selects how unit-aware scalar
fields appear in `repr()`. It does not change stored values or Pydantic
serialization.

| Strategy | Representation | Choose it when |
| --- | --- | --- |
| {py:attr}`DEVICE_BASE <infrasys.units.UnitSystem.DEVICE_BASE>` | Per-unit against each field's device base. This is the default. | You want to compare values relative to each device's own rating or base. |
| {py:attr}`SYSTEM_BASE <infrasys.units.UnitSystem.SYSTEM_BASE>` | Per-unit against the bound system base. If no base is bound, output falls back to device-base per-unit. | You want to compare power values against a common system base. The same numeric base is applied to every relative field, so avoid this mode for mixed-dimension models such as those with both power and voltage fields. |
| {py:attr}`NATURAL_UNITS <infrasys.units.UnitSystem.NATURAL_UNITS>` | The per-unit value multiplied by its device base and labeled with the base field's unit. | Physical-unit magnitudes are easier to interpret than per-unit values. Compound rates currently omit their residual time unit in `repr()`. |

For example, this power-only model can be displayed against its device base,
a shared system base, or in natural units:

```python
from typing import Annotated

from infrasys.units import HasPerUnit, Unit, UnitSystem, unit_system


class PowerDevice(HasPerUnit):
    base_power: Annotated[float, Unit("MVA")]
    rating: Annotated[float, Unit("pu", base="base_power")]


power_device = PowerDevice(base_power=100.0, rating=0.5)
power_device.bind_system_base(200.0)
for mode in (UnitSystem.DEVICE_BASE, UnitSystem.SYSTEM_BASE, UnitSystem.NATURAL_UNITS):
    with unit_system(mode):
        print(f"{mode.value}: {power_device!r}")
```

```text
DEVICE_BASE: PowerDevice(base_power='100.0 MVA', rating='0.5 pu')
SYSTEM_BASE: PowerDevice(base_power='100.0 MVA', rating='0.25 pu (system)')
NATURAL_UNITS: PowerDevice(base_power='100.0 MVA', rating='50 MVA')
```

Use {py:func}`set_unit_system <infrasys.units.set_unit_system>` to select and
return the active global strategy. Use
{py:func}`unit_system <infrasys.units.unit_system>` for temporary changes that
restore the previous strategy automatically. For example, natural-unit
representation changes `repr(device)`, but not its JSON:

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
field's `MW` label, although the physical ramp is 10 MW/min. Neither strategy
changes the stored 0.1 or its rate contract. This model has no unit-aware JSON
context that switches these scalar dumps to natural-unit strings or mappings;
{py:func}`unit_system <infrasys.units.unit_system>` is not a Pydantic
serialization option.

## Explicit configuration in the unit API

The low-level
{py:func}`convert_to_internal <infrasys.units.conversion.convert_to_internal>`
and
{py:func}`format_for_display <infrasys.units.conversion.format_for_display>`
functions live in {py:mod}`infrasys.units.conversion`. They take `value`
positionally and keep configuration keyword-only. The exported
{py:class}`UnitInput <infrasys.units.schema.UnitInput>` type describes mapping
inputs to {py:func}`~infrasys.units.conversion.convert_to_internal`. The
{py:func}`~infrasys.units.Unit` helper and
{py:class}`~infrasys.units.UnitSpec` take the unit name first and the base-field
reference as a keyword-only argument.

This mapping is the interface between input parsing and Pydantic validation and
serialization. {py:class}`UnitInput <infrasys.units.schema.UnitInput>` describes
its current shape. Shared key constants keep the implementation aligned, so a
future serialization change can update the contract and Pydantic hooks
deliberately. The {py:data}`VALUE_KEY <infrasys.units.schema.VALUE_KEY>` and
{py:data}`UNIT_KEY <infrasys.units.schema.UNIT_KEY>` constants use
[typing.Final](https://docs.python.org/3/library/typing.html#typing.Final) so
type checkers catch accidental reassignment. `Final` has no runtime effect.

## Other input and persistence contracts

The mixin precedes `BaseModel` or
{py:class}`~infrasys.component.Component` in the inheritance list. Numeric
inputs are already in internal units. A scalar `{value, unit}` mapping
converts compatible units; a field with a base divides by that base. Declare
annotated base fields before dependent fields. A zero, missing or nonfinite
base fails natural-input conversion. Rates preserve the declared residual time
unit.

Structured model inputs with a base divide their numeric fields by that base.
For nested {py:class}`~infrasys.units.HasUnits` models, only absolute unit fields
are divided. Unannotated numeric fields and nested per-unit fields are preserved.
Structured dumps use `{"value": { ... }, "unit": "pu"}` so revalidation does
not divide twice. This mapping is distinct from
{py:meth}`BaseQuantity.to_dict <infrasys.base_quantity.BaseQuantity.to_dict>`'s
`{value, units}` format. Optional values remain `None`. Scalar dumps remain
internal numbers. Structured conversion accepts alias-only fields and keeps
Pydantic validation active when rebuilding converted values. Serialization
exclusions do not remove required inputs during conversion.

{py:data}`ureg <infrasys.units.ureg>` is the registry used by
{py:class}`~infrasys.base_quantity.BaseQuantity`. The annotation API stores
numbers, while {py:class}`~infrasys.base_quantity.BaseQuantity` stores Pint
quantities with their units and serialization contract. Neither API silently
replaces the other.

{py:func}`~infrasys.units.unit_system` temporarily controls representation only.
The display state is process-global and is not task-local.
{py:meth}`HasPerUnit.bind_system_base <infrasys.units.HasPerUnit.bind_system_base>`
supplies instance-local display state. Infrasys
{py:class}`System <infrasys.system.System>` does not select or bind a power-system
base automatically. Unit display honors `Field(repr=False)`. Structured values
retain their native model representation in every display mode; only scalar
values receive numeric unit formatting. The initial migration preserves the
source formatting limitations for compound rates.
