# infrasys

[![CI](https://github.com/NREL/infrasys/actions/workflows/ci.yml/badge.svg)](https://github.com/NREL/infrasys/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/NREL/infrasys/branch/main/graph/badge.svg)](https://codecov.io/gh/NREL/infrasys)
[![PyPI](https://img.shields.io/pypi/v/infrasys.svg)](https://pypi.org/project/infrasys/)
[![Ruff](https://img.shields.io/badge/Ruff-&gt;=_0.0-blue?logo=ruff&logoColor=white)](https://github.com/charliermarsh/ruff)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python)](https://www.python.org/)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-%23FE5196?logo=conventionalcommits&logoColor=white)](https://conventionalcommits.org)
[![License](https://img.shields.io/badge/license-BSD%203--Clause-blue)](LICENSE.txt)
[![Docs](https://img.shields.io/badge/docs-GitHub-blue)](https://github.com/NREL/infrasys/tree/main/docs)
[![release-please](https://github.com/NREL/infrasys/actions/workflows/release.yaml/badge.svg)](https://github.com/NREL/infrasys/actions/workflows/release.yaml)
[![Docs (GitHub Pages)](https://github.com/NREL/infrasys/actions/workflows/gh-pages.yml/badge.svg)](https://github.com/NREL/infrasys/actions/workflows/gh-pages.yml)

infrasys is a lightweight data store that keeps track of components, their attributes, and
time series for energy infrastructure models. The core package is opinionated about validation,
unit handling, and data migration so that downstream modeling packages can focus on solving
their domain problems instead of managing persistence concerns.

## Highlights

- **Typed components with pint validation:** Base models derive from `pydantic` and use
  `pint` quantities whenever a physical unit is involved.
- **Efficient time-series storage:** Time series arrays and their metadata are stored by the
  Rust-backed `infrastore` package (NetCDF arrays plus a SQLite database).
- **Efficient serialization:** Components, supplemental attributes, and nested systems are
  serialized to JSON with automatic metadata and optional migration hooks.
- **Designed for extension:** Derive your own `System` classes, override component addition
  logic, or ship supplemental attributes alongside the core storage.

## Getting started

### Install

```bash
pip install git+https://github.com/NREL/infrasys.git@main
```

Don’t forget to install pre-commit hooks so your push meets project quality checks:

```bash
pre-commit install
```

### Quick example

```python
from infrasys import Component, System
from infrasys.location import Location


class Bus(Component):
    voltage: float
    location: Location | None = None


system = System(name="demo-grid")
bus = Bus(name="bus-1", voltage=1.05, location=Location(x=0.0, y=0.0))
system.add_components(bus)
system.to_json("demo-grid/system.json")
```

Instantiate a `System`, add a few components, and dump everything to JSON. Time series data
gets written to a sibling directory alongside the JSON file so you can externalize it with
`System.to_json(...)` and `System.from_json(...)`.

## Documentation

- **How To guides:** step-by-step recipes in `docs/how_tos`.
- **Tutorials:** opinionated walkthroughs for custom systems under `docs/tutorials`.
- **API Reference:** auto-generated reference material lives in `docs/reference`.
- **Explanation articles:** deeper dives on the time-series storage backend, serialization, and
  behavior in `docs/explanation`.

To build the docs locally, install `docs` extras and run `make html` from the `docs` directory.

## Development

- Clone this repository and install the dev dependency group before hacking:

```bash
pip install -e ".[dev]"
```

- Run the test suite and coverage reporting via:

```bash
pytest
```

### Building the Rust `infrastore` extension

infrasys depends on the Rust `infrastore` package for its time series backend. During
local development this is resolved from a sibling checkout via [`[tool.uv.sources]`](pyproject.toml):

```toml
[tool.uv.sources]
infrastore = { path = "../infrastore/crates/infrastore-py" }
```

1. Clone the [`infrastore`](https://github.com/NatLabRockies/infrastore) repository
   next to this one so the relative path resolves:

   ```bash
   git clone https://github.com/NatLabRockies/infrastore.git ../infrastore
   ```

2. Make sure a Rust toolchain is installed (`uv` compiles the extension with `maturin`):

   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

3. Sync the environment. `uv` builds the Rust extension and installs everything from `uv.lock`:

   ```bash
   uv sync
   ```

4. **After editing the Rust source**, rebuild and reinstall the compiled extension — a plain
   `uv sync` will not rebuild an unchanged-version path dependency:

   ```bash
   uv sync --reinstall-package infrastore
   ```

   You can confirm the rebuild picked up your changes, e.g.:

   ```bash
   uv run python -c "import time_series_store as t; print(t.TimeSeriesStore.add_time_series.__doc__)"
   ```

With the environment synced, run the suite through `uv`:

```bash
uv run pytest
```

- Formatting and linting are managed by `ruff` and configured through its `pyproject.toml` section.
  Keep your hooks healthy by installing them via `pre-commit install` (see Getting started) and running
  `pre-commit run --all-files` before pushing.

## Support & Contribution

infrasys is being developed under NREL Software Record SWR-24-42. Report issues and feature
requests at [https://github.com/NatLabRockies/infrasys/issues](https://github.com/NatLabRockies/infrasys/issues).
Review the `docs/reference` and `docs/how_tos` material before submitting a change so your
diff is aligned with the project conventions.

## License

infrasys is released under the BSD 3-Clause License. See
[LICENSE.txt](LICENSE.txt) for details.
