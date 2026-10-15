# Running benchmarks

We created some scripts that track some of the software decision.

`scripts`

How to run

```console
uv run pytest scripts/$SCRIPT
```

# How to Benchmark Different Python Implementations with `pytest-benchmark`

This guide will walk you through setting up and running performance benchmarks
using `pytest-benchmark`. Benchmarking is crucial for making informed decisions
about which libraries or implementation strategies offer the best performance
for your specific use cases. We'll use the common example of comparing two JSON
serialization libraries: the standard `json` and the faster `orjson`.

## Why Benchmark?

When you have multiple ways to achieve the same task (e.g., using different
libraries or algorithms), benchmarks provide quantitative data on their
performance. This data helps you:

- Identify performance bottlenecks.
- Choose the most efficient library/method for critical code paths.
- Track performance regressions or improvements over time.
- Justify technical decisions with concrete evidence.

## Prerequisites

Before you start, make sure you have the following installed in your Python environment:

1.  **Python**: (e.g., Python 3.8+)
2.  **`uv`**: Or your preferred Python package manager/runner.
3.  **`pytest`**: The testing framework.
4.  **`pytest-benchmark`**: The pytest plugin for benchmarking.
5.  **`orjson`**: The alternative JSON library we'll be testing against (the standard `json` library is built-in).

You can install the necessary Python packages using `uv`:

```console
uv pip install pytest pytest-benchmark orjson
```
