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
.. _time-series-transaction-api:
```

## Transaction

The transaction object for time series operations, yielded by
{py:meth}`infrasys.system.System.time_series_transaction`. Call the time series methods on
it to batch them together and to make them share one unit of rollback.

```{eval-rst}
.. autoclass:: infrasys.time_series_transaction.TimeSeriesTransaction
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
