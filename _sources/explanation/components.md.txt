```{eval-rst}
.. _components-page:
```
# Components
A component is any element that is attached to a system.

All components are required to define a name as a string (it is required in the base class). This
may not be appropriate for all classes. The `Location` class in this package is one example. In
cases like that developers can define their own name field and set its default value to `""`.

Refer to the [Components API](#components-api) for more information.

## Inheritance
Recommended rule: A `Component` that has subclasses should never be directly instantiated.

Consider a scenario where a developer defines a `Load` class and then later decides a new load is
needed because of one custom field.

The temptation may be to create `CustomLoad(Load)`. This is very problematic in the design of
the infrasys API. There will be no way to retrieve only `Load` instances. Consider this example:

```python
for load in system.get_components(Load)
    print(load.name)
```

This will retrieve both `Load` and `CustomLoad` instances.

Instead, our recommendation is to create a base class with the common fields.

```python
class LoadBase(Component)
    """Defines common fields for all Loads."""

    common_field1: float
    common_field2: float

class Load(LoadBase):
    """A load component"""

class CustomLoad(LoadBase):
    """A custom load component"""

    custom_field: float
```
