"""Tests that a time series round-trips in the spelling its timestamps arrived in.

The store records how each series' timestamps were written --- an instant in UTC, an
instant at a fixed offset, an instant in a named IANA zone, or a wall clock naming no
instant --- and hands the same spelling back. infrasys neither attaches a zone to a
naive timestamp nor strips one from an aware timestamp, so what comes out of a read
equals what went into the write.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from infrastore import InvalidParameterError

from infrasys.exceptions import ISConflictingArguments
from infrasys.time_series_models import (
    Deterministic,
    NonSequentialTimeSeries,
    SingleTimeSeries,
    TimeSeriesStorageType,
)

from .models.simple_system import SimpleBus, SimpleGenerator, SimpleSystem

DENVER = ZoneInfo("America/Denver")
RESOLUTION = timedelta(hours=1)
LENGTH = 12

# Every spelling the store distinguishes, all naming the same wall clock.
SPELLINGS = {
    "zoneless": datetime(2024, 1, 1),
    "utc": datetime(2024, 1, 1, tzinfo=timezone.utc),
    "fixed_offset": datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-7))),
    "named_zone": datetime(2024, 1, 1, tzinfo=DENVER),
}


def make_system(tmp_path, count: int = 1):
    system = SimpleSystem(
        time_series_storage_type=TimeSeriesStorageType.TIME_SERIES_STORE,
        time_series_directory=tmp_path,
    )
    bus = SimpleBus(name="bus", voltage=1.0)
    system.add_component(bus)
    generators = [
        SimpleGenerator(name=f"gen{i}", active_power=1.0, rating=1.0, bus=bus, available=True)
        for i in range(count)
    ]
    system.add_components(*generators)
    return system, generators


@pytest.mark.parametrize("initial_timestamp", SPELLINGS.values(), ids=SPELLINGS)
def test_single_time_series_round_trips_its_spelling(tmp_path, initial_timestamp):
    system, (generator,) = make_system(tmp_path)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", initial_timestamp, RESOLUTION), generator
    )

    stored = system.get_time_series(generator, name="load")
    assert stored.initial_timestamp == initial_timestamp
    assert stored.initial_timestamp.tzinfo == initial_timestamp.tzinfo
    assert stored.initial_timestamp.utcoffset() == initial_timestamp.utcoffset()


@pytest.mark.parametrize("initial_timestamp", SPELLINGS.values(), ids=SPELLINGS)
def test_spelling_survives_save_and_reload(tmp_path, initial_timestamp):
    system, (generator,) = make_system(tmp_path / "live")
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", initial_timestamp, RESOLUTION), generator
    )
    system.to_json(tmp_path / "system.json")

    # The reload rebuilds the index from the store's catalog, which renders the instant
    # and records the spelling in separate columns; both are needed to reconstruct this.
    restored = SimpleSystem.from_json(tmp_path / "system.json")
    stored = restored.get_time_series(restored.get_component(SimpleGenerator, "gen0"), name="load")
    assert stored.initial_timestamp == initial_timestamp
    assert stored.initial_timestamp.tzinfo == initial_timestamp.tzinfo


@pytest.mark.parametrize("initial_timestamp", SPELLINGS.values(), ids=SPELLINGS)
def test_sliced_read_keeps_the_spelling(tmp_path, initial_timestamp):
    system, (generator,) = make_system(tmp_path)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", initial_timestamp, RESOLUTION), generator
    )

    start_time = initial_timestamp + 2 * RESOLUTION
    sliced = system.get_time_series(generator, name="load", start_time=start_time, length=3)
    assert sliced.initial_timestamp == start_time
    assert sliced.initial_timestamp.tzinfo == initial_timestamp.tzinfo
    assert list(sliced.data) == [2.0, 3.0, 4.0]


def test_naive_timestamps_are_stored_as_wall_clocks(tmp_path):
    """A naive timestamp claims no instant, and the catalog records exactly that."""
    system, (generator,) = make_system(tmp_path)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.arange(LENGTH, dtype=np.float64), "load", datetime(2024, 1, 1), RESOLUTION
        ),
        generator,
    )
    (record,) = system._time_series_mgr._storage.store.list_time_series()
    assert record["time_reference"] == "zoneless"
    # No trailing offset: a `Z` here would assert an instant the row does not name.
    assert record["initial_timestamp"] == "2024-01-01T00:00:00"


@pytest.mark.parametrize(
    ("initial_timestamp", "expected"),
    [
        (SPELLINGS["utc"], "utc"),
        (SPELLINGS["fixed_offset"], "-07:00"),
        (SPELLINGS["named_zone"], "America/Denver"),
    ],
    ids=["utc", "fixed_offset", "named_zone"],
)
def test_aware_timestamps_record_how_they_were_written(tmp_path, initial_timestamp, expected):
    system, (generator,) = make_system(tmp_path)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.arange(LENGTH, dtype=np.float64), "load", initial_timestamp, RESOLUTION
        ),
        generator,
    )
    (record,) = system._time_series_mgr._storage.store.list_time_series()
    assert record["time_reference"] == expected


def test_slice_of_a_zoned_series_steps_instants_across_a_transition(tmp_path):
    """A grid steps instants, not wall clocks, and a DST transition proves which.

    Denver's 2024-03-10 spring-forward skips the 02:00 hour, so the 30th hourly step
    from midnight on the 9th is 07:00 local, not 06:00. Python's own arithmetic on two
    ``ZoneInfo`` datetimes would answer 06:00 --- it subtracts wall clocks whenever both
    sides share a ``tzinfo`` --- so this pins the slice to the instants the store holds.
    """
    system, (generator,) = make_system(tmp_path)
    initial_timestamp = datetime(2024, 3, 9, tzinfo=DENVER)
    data = np.arange(72, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", initial_timestamp, RESOLUTION), generator
    )

    start_time = (initial_timestamp.astimezone(timezone.utc) + 30 * RESOLUTION).astimezone(DENVER)
    assert start_time == datetime(2024, 3, 10, 7, tzinfo=DENVER)

    sliced = system.get_time_series(generator, name="load", start_time=start_time, length=4)
    assert sliced.initial_timestamp == start_time
    assert list(sliced.data) == [30.0, 31.0, 32.0, 33.0]


def test_start_time_must_be_spelled_like_the_series(tmp_path):
    system, (generator,) = make_system(tmp_path)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.arange(LENGTH, dtype=np.float64), "load", SPELLINGS["utc"], RESOLUTION
        ),
        generator,
    )
    with pytest.raises(ISConflictingArguments, match="spelled differently"):
        system.get_time_series(
            generator, name="load", start_time=datetime(2024, 1, 1, 2), length=2
        )


@pytest.mark.parametrize("initial_timestamp", SPELLINGS.values(), ids=SPELLINGS)
def test_forecast_round_trips_its_spelling(tmp_path, initial_timestamp):
    system, (generator,) = make_system(tmp_path)
    forecast = Deterministic(
        name="load",
        data=np.ones((4, 3)),
        initial_timestamp=initial_timestamp,
        resolution=RESOLUTION,
        horizon=3 * RESOLUTION,
        interval=RESOLUTION,
        window_count=4,
    )
    system.add_time_series(forecast, generator)

    stored = system.get_time_series(generator, name="load")
    assert stored.initial_timestamp == initial_timestamp
    assert stored.initial_timestamp.tzinfo == initial_timestamp.tzinfo


@pytest.mark.parametrize("initial_timestamp", SPELLINGS.values(), ids=SPELLINGS)
def test_non_sequential_time_series_round_trips_its_spelling(tmp_path, initial_timestamp):
    system, (generator,) = make_system(tmp_path)
    timestamps = [initial_timestamp + hours * RESOLUTION for hours in (0, 3, 9)]
    system.add_time_series(
        NonSequentialTimeSeries.from_array([1.0, 2.0, 3.0], timestamps, "load"), generator
    )

    stored = system.get_time_series(generator, name="load")
    assert list(stored.timestamps) == timestamps
    assert [t.tzinfo for t in stored.timestamps] == [initial_timestamp.tzinfo] * 3


def test_non_sequential_time_series_rejects_mixed_spellings():
    with pytest.raises(ValueError, match="spelled inconsistently"):
        NonSequentialTimeSeries.from_array(
            [1.0, 2.0],
            [datetime(2024, 1, 1), datetime(2024, 1, 1, 1, tzinfo=timezone.utc)],
            "load",
        )


def test_reader_refuses_a_cohort_that_mixes_spellings(tmp_path):
    """One reader materializes one timestamp axis, so it cannot span both groups."""
    system, (naive_owner, aware_owner) = make_system(tmp_path, count=2)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", SPELLINGS["zoneless"], RESOLUTION), naive_owner
    )
    system.add_time_series(
        SingleTimeSeries.from_array(data + 100, "load", SPELLINGS["utc"], RESOLUTION), aware_owner
    )

    with pytest.raises(InvalidParameterError, match="one spelling"):
        system.build_time_series_reader(RESOLUTION, name="load")


def test_zoneless_filter_splits_a_mixed_system_into_two_readers(tmp_path):
    system, (naive_owner, aware_owner) = make_system(tmp_path, count=2)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "load", SPELLINGS["zoneless"], RESOLUTION), naive_owner
    )
    system.add_time_series(
        SingleTimeSeries.from_array(data + 100, "load", SPELLINGS["utc"], RESOLUTION), aware_owner
    )

    wall_clocks = system.build_time_series_reader(RESOLUTION, name="load", zoneless=True)
    assert wall_clocks.component_ids == (naive_owner.id,)
    assert wall_clocks.timestamps[0] == SPELLINGS["zoneless"]
    assert wall_clocks.read(wall_clocks.timestamps[0]) == {naive_owner.id: 0.0}

    instants = system.build_time_series_reader(RESOLUTION, name="load", zoneless=False)
    assert instants.component_ids == (aware_owner.id,)
    assert instants.timestamps[0] == SPELLINGS["utc"]
    assert instants.read(instants.timestamps[0]) == {aware_owner.id: 100.0}


def test_forecast_reader_accepts_the_zoneless_filter(tmp_path):
    system, (naive_owner, aware_owner) = make_system(tmp_path, count=2)
    for owner, initial_timestamp in (
        (naive_owner, SPELLINGS["zoneless"]),
        (aware_owner, SPELLINGS["utc"]),
    ):
        system.add_time_series(
            Deterministic(
                name="load",
                data=np.ones((4, 3)),
                initial_timestamp=initial_timestamp,
                resolution=RESOLUTION,
                horizon=3 * RESOLUTION,
                interval=RESOLUTION,
                window_count=4,
            ),
            owner,
        )

    with pytest.raises(InvalidParameterError, match="one spelling"):
        system.build_forecast_reader(RESOLUTION, name="load")

    instants = system.build_forecast_reader(RESOLUTION, name="load", zoneless=False)
    assert instants.component_ids == (aware_owner.id,)
    assert instants.timestamps[0] == SPELLINGS["utc"]


def test_readers_hand_back_timestamps_they_accept(tmp_path):
    """The reader's own axis is always spelled the way its ``read`` expects."""
    system, (generator,) = make_system(tmp_path)
    system.add_time_series(
        SingleTimeSeries.from_array(
            np.arange(LENGTH, dtype=np.float64), "load", SPELLINGS["named_zone"], RESOLUTION
        ),
        generator,
    )

    reader = system.build_time_series_reader(RESOLUTION, name="load")
    assert reader.timestamps[0] == SPELLINGS["named_zone"]
    for step, when in enumerate(reader.timestamps):
        assert reader.read(when) == {generator.id: float(step)}


def test_one_owner_can_hold_both_spellings(tmp_path):
    """The spelling is per series, so a Denver profile sits beside a zoneless one."""
    system, (generator,) = make_system(tmp_path)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "wall_clock", SPELLINGS["zoneless"], RESOLUTION),
        generator,
    )
    system.add_time_series(
        SingleTimeSeries.from_array(data, "instants", SPELLINGS["named_zone"], RESOLUTION),
        generator,
    )

    # An unsliced bulk read spans both groups: with no bound there is nothing for them to
    # disagree about, and each series carries its own spelling back.
    by_name = {series.name: series for series in system.list_time_series(generator)}
    assert by_name["wall_clock"].initial_timestamp == SPELLINGS["zoneless"]
    assert by_name["instants"].initial_timestamp == SPELLINGS["named_zone"]

    keys = {key.name: key for key in system.list_time_series_keys(generator)}
    assert keys["wall_clock"].initial_timestamp.tzinfo is None
    assert keys["instants"].initial_timestamp.tzinfo == DENVER


def test_a_sliced_bulk_read_cannot_span_both_spellings(tmp_path):
    """One `start_time` is one request, and the two groups have no shared bound."""
    system, (generator,) = make_system(tmp_path)
    data = np.arange(LENGTH, dtype=np.float64)
    system.add_time_series(
        SingleTimeSeries.from_array(data, "wall_clock", SPELLINGS["zoneless"], RESOLUTION),
        generator,
    )
    system.add_time_series(
        SingleTimeSeries.from_array(data, "instants", SPELLINGS["utc"], RESOLUTION), generator
    )

    with pytest.raises(ISConflictingArguments, match="spelled differently"):
        system.list_time_series(generator, start_time=datetime(2024, 1, 1, 2), length=3)

    # Narrowed to one series, the same bound is fine.
    (sliced,) = system.list_time_series(
        generator, name="wall_clock", start_time=datetime(2024, 1, 1, 2), length=3
    )
    assert sliced.initial_timestamp == datetime(2024, 1, 1, 2)
    assert list(sliced.data) == [2.0, 3.0, 4.0]
