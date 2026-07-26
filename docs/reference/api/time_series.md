```{eval-rst}
.. _time-series-api:
```
# Time Series

```{eval-rst}
.. autopydantic_model:: infrasys.time_series_models.TimeSeriesData
   :members:
```

```{eval-rst}
.. _singe-time-series-api:
```

```{eval-rst}
.. autopydantic_model:: infrasys.time_series_models.SingleTimeSeries
   :members:
```

```{eval-rst}
.. _nonsequential-time-series-api:
```

```{eval-rst}
.. autopydantic_model:: infrasys.time_series_models.NonSequentialTimeSeries
   :members:
   :model-show-json: False
```

```{eval-rst}
.. _deterministic-time-series-api:
```

```{eval-rst}
.. autopydantic_model:: infrasys.time_series_models.Deterministic
   :members:
```

```{eval-rst}
.. _time-series-context-api:
```

## Context

The transaction object for time series operations, returned by
{py:meth}`infrasys.system.System.open_time_series_store`. Pass it as ``context=`` to batch
calls together and to make them share one unit of rollback.

```{eval-rst}
.. autoclass:: infrasys.time_series_context.TimeSeriesStorageContext
   :members:
```

```{eval-rst}
.. _time-series-readers-api:
```

## Readers

Readers return every matched component's data at one timestamp, which is the transpose of
what the other read methods provide. Build them with
{py:meth}`infrasys.system.System.build_time_series_reader` and
{py:meth}`infrasys.system.System.build_forecast_reader`. See
[How to read time series by timestamp](#read-time-series-by-timestamp) for a worked example.

```{eval-rst}
.. autoclass:: infrasys.time_series_reader.TimeSeriesReader
   :members:
```

```{eval-rst}
.. autoclass:: infrasys.time_series_reader.ForecastReader
   :members:
```
