from datetime import datetime, timedelta

import numpy as np
import pytest

from infrasys.time_series_models import Deterministic
from tests.models.simple_system import SimpleGenerator, SimpleSystem


def test_deterministic_time_series_persistence_is_not_supported():
    system = SimpleSystem(auto_add_composed_components=True)
    gen = SimpleGenerator.example()
    system.add_components(gen)

    initial_time = datetime(year=2020, month=9, day=1)
    resolution = timedelta(hours=1)
    horizon = timedelta(hours=8)
    interval = timedelta(hours=1)
    window_count = 3

    forecast_data = [
        [100.0, 101.0, 101.3, 90.0, 98.0, 87.0, 88.0, 67.0],
        [101.0, 101.3, 99.0, 98.0, 88.9, 88.3, 67.1, 89.4],
        [99.0, 67.0, 89.0, 99.9, 100.0, 101.0, 112.0, 101.3],
    ]

    name = "active_power_forecast"
    ts = Deterministic.from_array(
        np.array(forecast_data),
        name,
        initial_time,
        resolution,
        horizon,
        interval,
        window_count,
    )
    with pytest.raises(
        NotImplementedError,
        match="Time-series persistence is not implemented for Deterministic",
    ):
        system.add_time_series(ts, gen)
