# The `units` column collision — infrasys vs. the planned user-declared units field

Status: **open, deferred** — written 2026-07-30, updated 2026-08-13. No code changed in
infrasys. Owner: unassigned.

**2026-08-13 update.** The store gained two new descriptor columns (`quantity_kind`,
`unit_system`) and renamed `ext` to `application_data` (`DATA_FORMAT_VERSION` 0.15.0). That
does not decide anything here, but it materially changes the option set below — see
[What changed on 2026-08-13](#what-changed-on-2026-08-13) at the end. Nothing in infrasys
breaks: its 248 tests pass unchanged against the new store, because infrasys never used
`ext`.

Note on citations: this file used to cite `infrastore-core` **line numbers**, which silently
went stale the moment that repo was refactored — exactly what happened in the 0.15.0 change.
They have been replaced with symbol names, which `grep` can still resolve.

## The agreed design (elsewhere in the stack)

`units` is to become a **user-declared label on every time series struct**, in all four
layers (IS.jl, InfraStore.jl, infrastore/Rust, infrasys). The agreed semantics:

- Set by the **user** at construction time, defaulting to `None`/`nothing`/`null`:
  `SingleTimeSeries(data, name, units="MW")`. This is what distinguishes it from
  `element_type`, which a *package* derives from the array and a user never touches.
- **Immutable** after creation. No setter.
- **Not filterable** and **not identity**: it never appears in a `TimeSeriesKey`, never in
  a lookup or a `get_time_series*` parameter. Two series that differ only in `units` are a
  duplicate, not two series. (The store already agrees: `units` is in
  `RESERVED_FEATURE_NAMES` in `infrastore-core/src/types/metadata.rs` — so it cannot be
  smuggled in as a feature.)
- **Returned to the user on read**, on the reconstructed struct.
- **No vocabulary** in infrastore. IS.jl may grow one later; the store stores an opaque
  string and never interprets it.

The store side already supports all of this: `units TEXT` exists on
`time_series_associations` (the `DDL` in `infrastore-core/src/metadata/schema.rs`),
`TimeSeriesMetadata.units` exists (`types/metadata.rs`), and a derived
`DeterministicSingleTimeSeries` inherits it for free via `..src.clone()`. The work is API
plumbing, not schema — no `DATA_FORMAT_VERSION` bump.

**Implemented for Julia and Rust (2026-07-30/31).** `units` — along with `application_data`
(then spelled `ext`) and `element_type` — is now a field on the five time series structs in
IS.jl, InfraStore.jl, and the Rust core; `AddRequest` no longer carries any of the three.
Only infrasys is
outstanding, for the reason below.

## The problem

**infrasys already uses that column, for something else.**

Today infrasys writes a serialized `QuantityMetadata` blob into `units` and reads it back to
rehydrate `pint` quantities:

| Step | Location | What happens |
|---|---|---|
| Derive | `time_series_store_storage.py:997` (`_units_from_data`) | If `time_series.data` is a `pint.Quantity`, build `QuantityMetadata(module=…, quantity_type=…, units=str(data.units))`. Otherwise `None`. |
| Serialize | `:1091` (`_serialize_units`) | `orjson`-dump it to a string. |
| Write | `:264-265`, `:306` | The string goes into the add payload's `"units"` field. |
| Read back | `:865` (`_deserialize_units`) | Parse the column back into a `QuantityMetadata` when hydrating the association index. |
| Rehydrate | `:730-731` (`_build_result`) | `data = stored.units.quantity_type(data, stored.units.units)` — reconstructs the original quantity subclass on every read. |
| Expose | `time_series_reader.py:52` (`TimeSeriesReader.units`), `:142` (`ForecastReader.units`) | Both readers hand the per-component `QuantityMetadata` to the caller, since the stepping readers return raw magnitudes. |

The model is at `time_series_models.py:327`:

```python
class QuantityMetadata(InfraSysBaseModel):
    module: str
    quantity_type: Annotated[Type, WithJsonSchema({"type": "string"})]
    units: str
```

So the column holds `{"module": "infrasys.quantities", "quantity_type": "ActivePower",
"units": "MW"}`, not `"MW"`.

### Why this is a real conflict, not a naming nit

1. **One column, two payloads.** A plain label and a JSON blob cannot both live in
   `units`. Whichever wins, the other needs a new home.
2. **Derived vs. declared.** infrasys *infers* units from the data (`pint.Quantity`); the
   agreed design has the *user declare* them. Both are defensible. In Python, passing a
   `pint.Quantity` arguably already **is** the declaration, which makes a second, parallel
   `units: str | None` field a second way to say the same thing — with no rule for what
   happens when they disagree (`data` in kilowatts, `units="MW"`).
3. **Cross-language readability breaks.** This is the sharp edge. A store written by
   infrasys and read by IS.jl surfaces a JSON blob where the Julia side expects a label,
   and vice versa. The whole point of putting `units` in the store rather than in each
   binding's private `application_data` is that every consumer can read it. Today infrasys silently
   makes that false.
4. **`QuantityMetadata` is load-bearing.** It is not just a label — `module` and
   `quantity_type` are what let a read reconstruct a *custom* `BaseQuantity` subclass
   (`base_quantity.py`). Reducing it to a bare string loses that, and
   `tests/test_time_series_reader.py:123` (`test_reader_exposes_units`) plus the
   `_build_result` path depend on it.

## Options considered (none chosen)

**A. Move `QuantityMetadata` to `application_data`; `units` becomes the plain label.**
(The column was called `ext` when this was written; 0.15.0 renamed it to
`application_data`, whose whole documented purpose is "an opaque, package-owned payload for
an application to reconstruct its own domain objects" — which is exactly what
`QuantityMetadata` is.) It is completely unused in infrasys today — a grep for it in
`time_series_store_storage.py` returns nothing — so the column is free. The pint round-trip
keeps working unchanged, just through a different column, and `units` becomes readable by
every binding. Cost: touches the add path, the index hydration at `:865`, `_build_result`,
and both readers. Still leaves open question (2) — what happens when declared units and
`pint` units disagree.

**B. Replace `QuantityMetadata` with the plain label.** Store only the string; derive it
from `pint.Quantity` when the user doesn't declare one. Simplest and most uniform, but
reads can no longer rebuild custom `BaseQuantity` subclasses — they'd come back as plain
arrays or bare pint quantities. That is a user-visible behavior regression in infrasys.

**C. infrasys opts out.** Only Julia and Rust get the user-declared field; infrasys keeps
its derived-from-pint behavior. Cheapest now, but it *is* the cross-language inconsistency
described in (3), just accepted deliberately rather than by accident.

## Related item — resolved

**Status: closed for Julia and Rust (2026-07-31).**

`element_type`, `units`, and `application_data` now live on the Rust `TimeSeriesData` variants rather
than on `AddRequest`. `Store::get_time_series` and `Store::bulk_read` both populate them
from the catalog row they already load; the bulk-result FFI getters return them through
nullable `out_application_data` / `out_element_type` / `out_units` params
(0.15.0 added `out_quantity_kind` / `out_unit_system` alongside them); and `InfraStore.jl`'s
`_bulk_*` decoders put them on the reconstructed struct. A bulk read and a per-key read of
the same series now produce equal structs, with a parity test in InfraStore.jl's suite.

This also closed the older `application_data`-drops-on-bulk-read gap, which had been silently true
since before `units` existed.

infrasys never hit this: it keeps its own association index and resolves units from there
(`_units_for_key` in `time_series_store_storage.py`) rather than from the read result.

## What to decide on revisit

1. Which column holds the pint reconstruction metadata, and which holds the label (A/B/C).
2. Precedence when a user declares `units=` **and** passes a `pint.Quantity` whose units
   differ: error, user wins, data wins, or the declared label is disallowed in Python.
3. Whether infrasys exposes `units` as a constructor argument at all, given that
   `pint.Quantity` already carries it.
4. ~~Whether the bulk-read gap is fixed for `units` + `application_data` together~~ — done for Julia and
   Rust; see "Related item" above. Nothing is needed in infrasys, which does not read
   descriptors off the bulk result.


## What changed on 2026-08-13

The store grew two descriptor columns and renamed a third. `DATA_FORMAT_VERSION` went to
`0.15.0`, so stores written by earlier versions are rejected on open — but **nothing in
infrasys breaks**: its full suite passes unchanged, because it never used the renamed
column.

| Column | Change | Bearing on this note |
|---|---|---|
| `ext` → `application_data` | renamed | Option A's destination now has a name that says what it is for |
| `quantity_kind` | new, free-form TEXT | Overlaps `QuantityMetadata.quantity_type` almost exactly |
| `unit_system` | new, `natural_units` \| `component_base` | New concept for infrasys's time-series path |

### Option A got substantially cheaper, and splits three ways

`application_data` is documented as "an opaque, package-owned payload stored verbatim for
an application to reconstruct its own domain objects; the store never parses or interprets
it." That is a verbatim description of `QuantityMetadata`. The column that option A wanted
is no longer merely *free*, it is *named for this*.

Better, the three fields of `QuantityMetadata` now have three natural homes rather than one
opaque one:

| `QuantityMetadata` field | Example | Home |
|---|---|---|
| `units` | `"MW"` | `units` — readable by every binding, which is the whole point |
| `quantity_type` | `ActivePower` | `quantity_kind` — its **name is already a QUDT `QuantityKind` local name** |
| `module` | `"infrasys.quantities"` | `application_data` — genuinely Python-private, and nothing else can use it |

That the `quantity_type` names line up with QUDT is a coincidence worth not wasting. Seven
of the eight classes in `infrasys/quantities.py` — `Distance`, `Voltage`, `Angle`,
`ActivePower`, `Energy`, `Time`, `Resistance` — are already QUDT `QuantityKind` local names
verbatim, so an infrasys store would become readable by IS.jl and the CLI without a
translation table. The eighth, `Current`, is QUDT's `ElectricCurrent`; that one name is the
whole cost of adopting the vocabulary, and the store does not enforce it either way (the
column is deliberately free-form).

Only `module` is then truly binding-private, which is a much smaller thing to hide in an
opaque column than the whole blob — and a Julia reader that meets it can ignore one unknown
key instead of failing to parse a label.

### What this still does not decide

Open questions 2 and 3 above are untouched. The store change makes the *plumbing* cheap; it
says nothing about precedence when a user declares `units=` and passes a `pint.Quantity`
that disagrees, nor whether infrasys should expose `units=` at all. Those remain the reason
this note is open.

### `unit_system` is a new concept here, and does not map cleanly

infrasys already has a `UnitSystem` StrEnum (`cost_curves.py`: `SYSTEM_BASE`,
`DEVICE_BASE`, `NATURAL_UNITS`), but it is used only for a cost curve's `power_units` — the
time-series path has no unit-system concept at all. Two mismatches to resolve before
adopting the column:

- **Naming.** The store spells the device base `component_base`, deliberately, because it
  addresses components rather than devices. `DEVICE_BASE` ↔ `component_base` needs an
  explicit mapping, not an assumption that the names match.
- **Arity.** The store represents **two** bases; infrasys and IS.jl both name **three**.
  `SYSTEM_BASE` has no storage spelling. IS.jl (2026-08-13) resolved this by *rejecting* a
  system-base series at the store boundary rather than downgrading it, on the grounds that
  a per-unit series read against the wrong base is wrong by a factor nobody can recover
  afterward. infrasys would need the same rule, or the store would need a third variant.

### Cross-repo state after this change

IS.jl and InfraStore.jl gained `quantity_kind` and `unit_system` on all five time-series
structs on 2026-08-13, mirroring how they already carried `units`. infrasys is now the only
layer without them. That widens the cross-language inconsistency described in problem (3)
above rather than narrowing it: a store written by IS.jl and read by infrasys now carries
two descriptors infrasys cannot see, on top of a `units` column the two sides already
disagree about.
