# Unit-aware models

`infrasys.units` provides `HasUnits`, `HasPerUnit`, `Unit` (an alias for
`unit_spec`), `UnitSpec`, `UnitSystem`, `get_unit_system`, `set_unit_system`,
`unit_system`, and `ureg`. They are also available from `infrasys`.

```python
from typing import Annotated
from pydantic import BaseModel
from infrasys.units import HasUnits, Unit

class Device(HasUnits, BaseModel):
    base_power: Annotated[float, Unit("MW")]
    power: Annotated[float, Unit("pu", base="base_power")]
    ramp: Annotated[float, Unit("MW/min", base="base_power")]

device = Device(base_power=100, power={"value": 5000, "unit": "kW"},
                ramp={"value": 600, "unit": "MW/hour"})
assert device.power == 0.05
assert device.ramp == 0.1
```

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

`ureg` is the existing `BaseQuantity` registry. The annotation API stores numbers;
`BaseQuantity` continues to store Pint quantities and retains its serialization
contract. Neither API silently replaces the other.

`unit_system(mode)` temporarily controls representation only. The display state
is process-global, as in the original implementation, and is not task-local.
`HasPerUnit.bind_system_base(value)` supplies instance-local display state.
Infrasys `System` does not select or bind a power-system base automatically.
The initial migration preserves existing formatting, including its limitations
for compound rates and structured values; changing those semantics is separate
work.

The implementation and regression tests originate in r2x-core `feat/csv-2`
(PR #167) at `dd44fac8b231ab7bb1b96641b560e9617fca5725`, rather than R2X main.
At migration time, `feat/csv-2` was the only matching remote branch; an exact
lookup for `feat/csv` returned no branch. Infrasys does not import R2X.
R2X ingestion, parser policy, and automatic system-base binding remain in R2X.
