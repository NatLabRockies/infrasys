# Units

See the [unit-aware model guide](../units.md) for help choosing a field representation.

## Annotations and mapping types

```{eval-rst}
.. autofunction:: infrasys.units.Unit

.. autofunction:: infrasys.units.unit_spec

.. autoclass:: infrasys.units.UnitSpec

.. autoclass:: infrasys.units.schema.UnitInput
```

## Model mixins

```{eval-rst}
.. autoclass:: infrasys.units.HasUnits

.. autoclass:: infrasys.units.HasPerUnit

.. automethod:: infrasys.units.HasPerUnit.bind_system_base
```

## Representation modes

```{eval-rst}
.. autoclass:: infrasys.units.UnitSystem
   :members:

.. autofunction:: infrasys.units.get_unit_system

.. autofunction:: infrasys.units.set_unit_system

.. autofunction:: infrasys.units.unit_system
```

## Conversion functions and registry

```{eval-rst}
.. automodule:: infrasys.units.conversion
   :members: convert_to_internal, format_for_display

.. autodata:: infrasys.units.ureg
```

## Mapping keys

```{eval-rst}
.. autodata:: infrasys.units.schema.VALUE_KEY

.. autodata:: infrasys.units.schema.UNIT_KEY
```
