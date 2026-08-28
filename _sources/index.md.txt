# Infrastructure Systems

This package implements a data store for components and time series in support of Python-based
modeling packages. While it is designed to support teams modeling transmission and distribution
systems for electrical grids, it can be used by any package that needs to store components (e.g.,
generators and buses) that have quantities (e.g., power and voltage) which may vary over time.

## Features
- Stores components in data structures that provide fast lookup and iteration by type and name.
- Provides extendable data models that enable validation and unit conversion through
[pint](https://pint.readthedocs.io/en/stable/).
- Manages time series data efficiently. Data is only loaded into system memory when needed by
the user application.
- Manages serialization and de-serialization of components to JSON, including automatic handling of
nested objects.
- Enables data model migration.

```{eval-rst}
.. toctree::
    :maxdepth: 2
    :caption: Contents:
    :hidden:

    how_tos/index
    tutorials/index
    reference/index
    explanation/index
```

## How to use this guide
- Refer to [How Tos](#how-tos-page) for step-by-step instructions for managing a system.
- Refer to [Tutorials](#tutorials-page) examples of building and managing a system.
- Refer to [Reference](#reference-page) for API reference material.
- Refer to [Explanation](#explanation-page) for descriptions and behaviors of systems,
components, and time series.

# Indices and tables

- {ref}`genindex`
- {ref}`modindex`
- {ref}`search`
