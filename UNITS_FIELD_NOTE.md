# The `units` column collision — infrasys vs. the planned user-declared units field

Status: **open, deferred** — written 2026-07-30. No code changed in infrasys.
Owner: unassigned. Revisit before the user-declared `units` field ships in the other layers.

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
  `RESERVED_FEATURE_NAMES` — `infrastore-core/src/types/metadata.rs:158` — so it cannot be
  smuggled in as a feature.)
- **Returned to the user on read**, on the reconstructed struct.
- **No vocabulary** in infrastore. IS.jl may grow one later; the store stores an opaque
  string and never interprets it.

The store side already supports all of this: `units TEXT` exists on
`time_series_associations` (`infrastore-core/src/metadata/schema.rs:28`),
`TimeSeriesMetadata.units` exists (`types/metadata.rs:202`), and a derived
`DeterministicSingleTimeSeries` inherits it for free via `..src.clone()`. The work is API
plumbing, not schema — no `DATA_FORMAT_VERSION` bump.

**Implemented for Julia and Rust (2026-07-30/31).** `units` — along with `ext` and
`element_type` — is now a field on the five time series structs in IS.jl, InfraStore.jl,
and the Rust core; `AddRequest` no longer carries any of the three. Only infrasys is
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
   binding's private `ext` is that every consumer can read it. Today infrasys silently
   makes that false.
4. **`QuantityMetadata` is load-bearing.** It is not just a label — `module` and
   `quantity_type` are what let a read reconstruct a *custom* `BaseQuantity` subclass
   (`base_quantity.py`). Reducing it to a bare string loses that, and
   `tests/test_time_series_reader.py:123` (`test_reader_exposes_units`) plus the
   `_build_result` path depend on it.

## Options considered (none chosen)

**A. Move `QuantityMetadata` to `ext`; `units` becomes the plain label.**
`ext` is completely unused in infrasys today — a grep for it in
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

`element_type`, `units`, and `ext` now live on the Rust `TimeSeriesData` variants rather
than on `AddRequest`. `Store::get_time_series` and `Store::bulk_read` both populate them
from the catalog row they already load; the bulk-result FFI getters return them through
nullable `out_ext` / `out_element_type` / `out_units` params; and `InfraStore.jl`'s
`_bulk_*` decoders put them on the reconstructed struct. A bulk read and a per-key read of
the same series now produce equal structs, with a parity test in InfraStore.jl's suite.

This also closed the older `ext`-drops-on-bulk-read gap, which had been silently true
since before `units` existed.

infrasys never hit this: it keeps its own association index and resolves units from there
(`_units_for_key`, `:620`) rather than from the read result.

## What to decide on revisit

1. Which column holds the pint reconstruction metadata, and which holds the label (A/B/C).
2. Precedence when a user declares `units=` **and** passes a `pint.Quantity` whose units
   differ: error, user wins, data wins, or the declared label is disallowed in Python.
3. Whether infrasys exposes `units` as a constructor argument at all, given that
   `pint.Quantity` already carries it.
4. ~~Whether the bulk-read gap is fixed for `units` + `ext` together~~ — done for Julia and
   Rust; see "Related item" above. Nothing is needed in infrasys, which does not read
   descriptors off the bulk result.
