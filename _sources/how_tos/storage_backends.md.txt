# How to Use Different Storage Backends for Time Series Data

This guide explains how to use the different storage backends available in Infrasys for time series data. The backend you choose affects how time series data is stored and accessed throughout the system lifecycle.

## Available Storage Backends

Infrasys offers four different storage backends:

1. **In-Memory Storage** ({py:class}`~infrasys.in_memory_time_series_storage.InMemoryTimeSeriesStorage`): Stores time series data entirely in memory
2. **Arrow Storage** ({py:class}`~infrasys.arrow_storage.ArrowTimeSeriesStorage`): Stores time series data in Apache Arrow files on disk
3. **Chronify Storage** ({py:class}`~infrasys.chronify_time_series_storage.ChronifyTimeSeriesStorage`): Stores time series data in a SQL database using the Chronify library
4. **HDF5 Storage** (`HDF5TimeSeriesStorage`): Stores time series data in HDF5 files (available in development version)

## Choosing a Storage Backend

You can choose the storage backend when creating a {py:class}`~infrasys.system.System` by setting the `time_series_storage_type` parameter:

```python
from infrasys import System
from infrasys.time_series_models import TimeSeriesStorageType

# Create a system with in-memory storage
system_memory = System(time_series_storage_type=TimeSeriesStorageType.MEMORY)

# Create a system with Arrow storage (default)
system_arrow = System(time_series_storage_type=TimeSeriesStorageType.ARROW)

# Create a system with Chronify storage
system_chronify = System(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)

# Create a system with HDF5 storage (development version)
system_hdf5 = System(time_series_storage_type=TimeSeriesStorageType.HDF5)
```

```{note}
If you don't specify a storage type, Arrow storage is used by default.
```

## Storage Directory Configuration

For file-based storage backends (Arrow and Chronify), you can specify where the time series data will be stored:

```python
from pathlib import Path
from infrasys import System

# Use a specific directory for time series data
custom_dir = Path("/path/to/your/storage/directory")
system = System(time_series_directory=custom_dir)
```

```{tip}
If `time_series_directory` is not specified, a temporary directory will be created automatically. This directory will be cleaned up when the Python process exits.
```

```{warning}
If your time series data is in the range of GBs, you may need to specify an alternate location because the tmp filesystem may be too small.
```

## Converting Between Storage Types

You can convert between storage types during runtime using the `convert_storage` method:

```python
from infrasys.time_series_models import TimeSeriesStorageType

# Convert from in-memory to Arrow storage
system.convert_storage(time_series_storage_type=TimeSeriesStorageType.ARROW)

# Convert from Arrow to Chronify storage
system.convert_storage(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)
```

Here's a complete example of converting storage backends:

```python
from datetime import datetime, timedelta
import numpy as np
from infrasys.time_series_models import TimeSeriesStorageType, SingleTimeSeries
from infrasys import System
from tests.models.simple_system import SimpleSystem, SimpleBus, SimpleGenerator

# Create a system with in-memory storage
system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.MEMORY)

# Add components
bus = SimpleBus(name="test-bus", voltage=1.1)
generator = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
system.add_components(bus, generator)

# Create and add time series data
ts_data = SingleTimeSeries(
    data=np.arange(24),
    name="active_power",
    resolution=timedelta(hours=1),
    initial_timestamp=datetime(2020, 1, 1),
)
system.add_time_series(ts_data, generator, scenario="baseline")

# Verify storage type
print(f"Current storage type: {type(system._time_series_mgr._storage).__name__}")
# Output: Current storage type: InMemoryTimeSeriesStorage

# Convert to Arrow storage
system.convert_storage(time_series_storage_type=TimeSeriesStorageType.ARROW)

# Verify new storage type
print(f"New storage type: {type(system._time_series_mgr._storage).__name__}")
# Output: New storage type: ArrowTimeSeriesStorage

# Verify time series data is still accessible
ts = system.get_time_series(generator, variable_name="active_power", scenario="baseline")
print(f"Time series data preserved: {ts.data_array}")
# Output: Time series data preserved: [0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23]
```

```{tip}
Converting between storage types preserves all time series data. This can be useful when you need to optimize performance by switching storage strategies during different phases of your application.
```

## Choosing the Right Backend for Your Use Case

Each storage backend has different characteristics that make it suitable for different use cases:

### In-Memory Storage

**Best for:**

- Small datasets
- Quick prototyping and testing
- Temporary data that doesn't need to persist

**Characteristics:**

- Fastest access time
- Data is lost when the program exits
- Limited by available RAM

```python
system = System(time_series_storage_type=TimeSeriesStorageType.MEMORY)
```

### Arrow Storage

**Best for:**

- Datasets of any size
- Persistence across program runs
- Efficient file-based storage and retrieval
- Creates one file per time series array.

```{warning}
This can be problematic on HPC shared filesystems if the number of arrays is is greater than 10,000.
```

**Characteristics:**

- Fast file-based storage using Apache Arrow format
- Good balance of speed and persistence
- Default storage backend

```python
system = System(time_series_storage_type=TimeSeriesStorageType.ARROW)
```

### Chronify Storage

**Best for:**

- Complex time series data with relationships
- When SQL queries are needed
- Integration with database systems

**Characteristics:**

- Uses a SQL database via the Chronify library
- Supports transactional operations
- More powerful query capabilities

```python
system = System(time_series_storage_type=TimeSeriesStorageType.CHRONIFY)
```

### HDF5 Storage

**Best for:**

- Scientific datasets with three or more dimensions
- Data that benefits from HDF5's compression capabilities
- Systems with tens or hundreds of thousands of time series arrays
- Stores all time series arrays in one file.

**Characteristics:**

- Uses HDF5 file format, popular in scientific computing
- Supports hierarchical organization of data
- Good compression capabilities
- Compatible with [PowerSystems.jl](https://github.com/NREL-Sienna/PowerSystems.jl)

```python
system = System(time_series_storage_type=TimeSeriesStorageType.HDF5)
```

```{note}
HDF5 storage is currently available in the development version only.
```

## Working with Time Series Data

Regardless of the backend you choose, the API for adding, retrieving, and using time series data remains the same:

```python
from datetime import datetime, timedelta
import numpy as np
from infrasys.time_series_models import SingleTimeSeries
from tests.models.simple_system import SimpleSystem, SimpleGenerator, SimpleBus

# Create a system with your chosen backend
system = SimpleSystem(time_series_storage_type=TimeSeriesStorageType.ARROW)

# Add components
bus = SimpleBus(name="test-bus", voltage=1.1)
generator = SimpleGenerator(name="gen", active_power=1.0, rating=1.0, bus=bus, available=True)
system.add_components(bus, generator)

# Create and add time series data
length = 24
initial_time = datetime(year=2020, month=1, day=1)
resolution = timedelta(hours=1)
data = np.random.rand(length)

# Create a time series
time_series = SingleTimeSeries(
    data=data,
    name="active_power",
    resolution=resolution,
    initial_timestamp=initial_time
)

# Add the time series to a component
system.add_time_series(time_series, generator, scenario="baseline")

# Retrieve the time series later
retrieved_ts = system.get_time_series(
    generator,
    name="active_power",
    scenario="baseline"
)
```

## Read-Only Mode

For any storage backend, you can set it to read-only mode, which is useful when
you're working with existing data that won't or shouldn't be modified. For
example, suppose you want to load a system with GBs of time series data. By
default, infrasys will make a copy of the time series data during
de-serialization. If you set `time_series_read_only=True`, infrasys will skip
that copy operation.

```python
system = System(time_series_read_only=True)
```

```{warning}
In read-only mode, attempts to add or modify time series data will raise exceptions.
```

## Serializing and Deserializing a System

When saving a system to disk, all the time series data will be properly serialized regardless of the backend used:

```python
from pathlib import Path

# Save the entire system (including time series data)
output_dir = Path("my_system_data")
system.to_json(output_dir)

# To load the system back
loaded_system = SimpleSystem.from_json(output_dir)
```

```{note}
The storage backend information is preserved when saving and loading a system.
```

## Performance Considerations

Each storage backend offers different trade-offs in terms of performance:

- **Memory Usage**: In-memory storage keeps all data in RAM, which can be a limitation for large datasets
- **Disk Space**: Arrow, Chronify, and HDF5 storage use disk space, with different compression characteristics
- **Access Speed**: In-memory is fastest, followed by Arrow/HDF5, then Chronify (depending on the specific operation)
- **Query Flexibility**: Chronify offers the most complex query capabilities through SQL
- **Serialization/Deserialization Speed**: Arrow typically offers the fastest serialization for time series data

### Relative Performance Comparison

The table below gives a general comparison of the different storage backends (scale of 1-5, where 5 is best):

| Storage Type | Read Speed | Write Speed | Memory Usage | Disk Usage | Query Capabilities |
| ------------ | ---------- | ----------- | ------------ | ---------- | ------------------ |
| In-Memory    | 5          | 5           | 1            | N/A        | 2                  |
| Arrow        | 4          | 4           | 4            | 3          | 3                  |
| Chronify     | 2          | 3           | 4            | 3          | 5                  |
| HDF5         | 3          | 3           | 4            | 4          | 3                  |

```{note}
The above table is a generalization. Actual performance will depend on your specific dataset characteristics, hardware, and operations being performed.
```

### Benchmarking Your Use Case

For critical applications, it's recommended to benchmark different storage backends with your specific data patterns:

```python
import time
from datetime import datetime, timedelta
import numpy as np
from infrasys.time_series_models import TimeSeriesStorageType, SingleTimeSeries
from infrasys import System

# Function to benchmark storage operations
def benchmark_storage(storage_type, data_size=10000):
    # Setup
    system = System(time_series_storage_type=storage_type)

    # Generate test data
    data = np.random.random(data_size)
    ts = SingleTimeSeries(
        data=data,
        name="test_variable",
        resolution=timedelta(hours=1),
        initial_timestamp=datetime(2020, 1, 1),
    )

    # Benchmark write
    start_time = time.time()
    system.add_time_series(ts, system)
    write_time = time.time() - start_time

    # Benchmark read
    start_time = time.time()
    retrieved_ts = system.get_time_series(system, name="test_variable")
    read_time = time.time() - start_time

    return {"write_time": write_time, "read_time": read_time}

# Run benchmarks
results = {}
for storage_type in [
    TimeSeriesStorageType.MEMORY,
    TimeSeriesStorageType.ARROW,
    TimeSeriesStorageType.CHRONIFY
]:
    results[storage_type.name] = benchmark_storage(storage_type)

# Print results
for name, times in results.items():
    print(f"{name} - Write: {times['write_time']:.6f}s, Read: {times['read_time']:.6f}s")
```

Choose the storage backend that best meets your specific requirements for memory usage, persistence, access patterns, and query complexity.

## Summary

The Infrasys library provides multiple storage backends for time series data, each optimized for different use cases:

1. **In-Memory Storage**: Fastest but limited by RAM and lacks persistence
2. **Arrow Storage**: Good balance of speed and persistence, using Apache Arrow files
3. **Chronify Storage**: SQL-based storage with powerful query capabilities and time mappings.
4. **HDF5 Storage**: Hierarchical storage format compatible with [PowerSystems.jl](https://github.com/NREL-Sienna/PowerSystems.jl)

All storage backends implement the same interface, making it easy to switch between them as your needs change. The choice of storage backend doesn't affect how you interact with the time series data through the Infrasys API, but it can significantly impact performance and resource utilization.
