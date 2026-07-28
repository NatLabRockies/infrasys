"""Tests for the time series transaction facade and its backing context."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from infrasys.exceptions import ISAlreadyAttached, ISOperationNotAllowed
from infrasys.time_series_models import SingleTimeSeries

from .models.simple_system import SimpleBus, SimpleGenerator, SimpleSystem

INITIAL_TIMESTAMP = datetime(2024, 1, 1)
RESOLUTION = timedelta(hours=1)
LENGTH = 8


def make_system(tmp_path, count: int = 2):
    system = SimpleSystem(time_series_directory=tmp_path)
    bus = SimpleBus(name="bus", voltage=1.0)
    system.add_component(bus)
    generators = [
        SimpleGenerator(name=f"gen{i}", active_power=1.0, rating=1.0, bus=bus, available=True)
        for i in range(count)
    ]
    system.add_components(*generators)
    return system, generators


def make_series(name: str = "load", offset: float = 0.0) -> SingleTimeSeries:
    data = np.arange(LENGTH, dtype=np.float64) + offset
    return SingleTimeSeries.from_array(data, name, INITIAL_TIMESTAMP, RESOLUTION)


def test_staged_adds_are_visible_only_through_their_own_transaction(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.time_series_transaction() as txn:
        txn.add_time_series(make_series(), gen)
        # The transaction sees its own work.
        assert txn.has_time_series(gen, name="load")
        # A System call runs on its own and sees committed state only.
        assert not system.has_time_series(gen, name="load")

    # After the block commits, the addition is visible to everyone.
    assert system.has_time_series(gen, name="load")


def test_second_transaction_does_not_see_anothers_staged_adds(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.time_series_transaction() as writer:
        writer.add_time_series(make_series(), gen)
        with system.time_series_transaction() as reader:
            assert not reader.has_time_series(gen, name="load")

    assert system.has_time_series(gen, name="load")


def test_concurrent_contexts_each_commit_their_own_adds(tmp_path):
    """Two open batches must not observe or disturb each other's work.

    The facade always ties a batch to a store transaction, so independent batches are an
    internal capability: contexts created directly on the storage still buffer and
    commit independently of each other.
    """
    system, generators = make_system(tmp_path, count=2)
    first_gen, second_gen = generators
    manager = system.time_series

    first = manager.storage.new_context()
    second = manager.storage.new_context()

    first.add_time_series(make_series("first"), first_gen)
    second.add_time_series(make_series("second", offset=100.0), second_gen)

    # Neither batch has reached the store yet.
    assert not system.has_time_series(first_gen, name="first")
    assert not system.has_time_series(second_gen, name="second")

    first.commit()
    assert system.has_time_series(first_gen, name="first")
    assert not system.has_time_series(second_gen, name="second")

    second.commit()
    assert system.has_time_series(second_gen, name="second")


def test_nested_blocks_unwind_innermost_first(tmp_path):
    """Blocks nest LIFO: an inner failure undoes only its own work.

    Rollback is a store transaction, and SQLite savepoints are a stack, so two blocks
    open at once nest rather than running independently. `with` statements produce that
    shape naturally. Two *interleaved* batches that each commit or discard on their own
    schedule are not supported -- the price of exact rollback, including of removals,
    which a client-side undo log cannot deliver.
    """
    system, generators = make_system(tmp_path, count=2)
    outer_gen, inner_gen = generators

    with system.time_series_transaction() as outer:
        outer.add_time_series(make_series("outer"), outer_gen)
        with pytest.raises(RuntimeError):
            with system.time_series_transaction() as inner:
                inner.add_time_series(make_series("inner"), inner_gen)
                msg = "inner failed"
                raise RuntimeError(msg)
        # The outer block is still usable and still holds its own work.
        assert outer.has_time_series(outer_gen, name="outer")

    assert system.has_time_series(outer_gen, name="outer")
    assert not system.has_time_series(inner_gen, name="inner")


def test_rollback_restores_a_removal(tmp_path):
    """The capability the client-side undo log could not provide.

    Outside a transaction the store frees the array once its last association goes, so a
    removal is irreversible. Inside one the free is deferred to the commit, so the
    rollback restores the data and not merely the catalog row.
    """
    system, generators = make_system(tmp_path)
    gen = generators[0]
    system.add_time_series(make_series("keep"), gen)

    with pytest.raises(RuntimeError):
        with system.time_series_transaction() as txn:
            txn.remove_time_series(gen, name="keep")
            assert not txn.has_time_series(gen, name="keep")
            msg = "boom"
            raise RuntimeError(msg)

    assert system.has_time_series(gen, name="keep")
    restored = system.get_time_series(gen, name="keep")
    np.testing.assert_array_equal(restored.data, np.arange(LENGTH, dtype=np.float64))


def test_exception_undoes_adds_already_flushed_mid_block(tmp_path):
    """A read inside the block forces an early write; failing afterwards must undo it."""
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with pytest.raises(RuntimeError):
        with system.time_series_transaction() as txn:
            txn.add_time_series(make_series(), gen)
            # Reading requires the array to be in the store, so this flushes the batch.
            actual = txn.get_time_series(gen, name="load")
            assert actual.data[0] == 0.0
            msg = "fail the block after it was forced to write"
            raise RuntimeError(msg)

    assert not system.has_time_series(gen, name="load")
    assert system.time_series.storage.store.list_time_series() == []


def test_transaction_rejects_use_after_its_block_exits(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.time_series_transaction() as txn:
        txn.add_time_series(make_series(), gen)

    with pytest.raises(ISOperationNotAllowed):
        txn.add_time_series(make_series("other"), gen)


def test_context_rejects_use_against_a_different_system(tmp_path):
    """The internal ownership guard: a context is bound to one system's storage.

    Operations run through the context and so reach its own storage by construction. The
    one place the pairing can still go wrong is binding a context to another system's
    manager, which is where the guard lives.
    """
    first_system, _ = make_system(tmp_path / "first")
    second_system, _ = make_system(tmp_path / "second")

    foreign = first_system.time_series.storage.new_context()
    with pytest.raises(ISOperationNotAllowed):
        second_system.time_series.bind_context(foreign)


def test_duplicate_staged_on_one_transaction_is_rejected(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with pytest.raises(ISAlreadyAttached):
        with system.time_series_transaction() as txn:
            txn.add_time_series(make_series(), gen)
            txn.add_time_series(make_series(), gen)

    assert not system.has_time_series(gen, name="load")


def test_auto_flush_bounds_the_buffer(tmp_path):
    """A batch past the threshold drains mid-block instead of accumulating in memory."""
    system, generators = make_system(tmp_path)
    gen = generators[0]
    store = system.time_series.storage.store

    with system.time_series_transaction(auto_flush_threshold=3) as txn:
        for i in range(7):
            txn.add_time_series(make_series(f"ts_{i}"), gen)
        # Two auto-flushes at 3 and 6 drained all but the seventh entry.
        assert len(store.list_time_series()) == 6
        assert txn.has_staged_data
        # Flushed or buffered, everything stays visible through the transaction.
        for i in range(7):
            assert txn.has_time_series(gen, name=f"ts_{i}")

    assert len(store.list_time_series()) == 7


def test_auto_flush_by_bytes_bounds_large_arrays(tmp_path):
    """The byte limit flushes long series well before the count limit would."""
    system, generators = make_system(tmp_path)
    gen = generators[0]
    store = system.time_series.storage.store
    series_bytes = LENGTH * 8

    with system.time_series_transaction(auto_flush_bytes=3 * series_bytes) as txn:
        for i in range(7):
            txn.add_time_series(make_series(f"ts_{i}"), gen)
        # Byte-triggered flushes at 3 and 6 drained all but the seventh entry.
        assert len(store.list_time_series()) == 6
        assert txn.has_staged_data

    assert len(store.list_time_series()) == 7


def test_auto_flushed_work_rolls_back_with_the_block(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with pytest.raises(RuntimeError):
        with system.time_series_transaction(auto_flush_threshold=2) as txn:
            for i in range(5):
                txn.add_time_series(make_series(f"ts_{i}"), gen)
            msg = "boom"
            raise RuntimeError(msg)

    assert system.time_series.storage.store.list_time_series() == []


def test_transient_context_commits_each_call(tmp_path):
    """An operation with no transaction behaves exactly as it did before contexts existed."""
    system, generators = make_system(tmp_path)
    gen = generators[0]

    system.add_time_series(make_series(), gen)
    assert system.has_time_series(gen, name="load")
    assert len(system.time_series.storage.store.list_time_series()) == 1


def test_storage_holds_no_batch_state(tmp_path):
    """The storage object must not track transactions or staged work."""
    system, generators = make_system(tmp_path)
    storage = system.time_series.storage

    with system.time_series_transaction() as txn:
        txn.add_time_series(make_series(), generators[0])
        assert txn.has_staged_data
        # Nothing about the open batch is reachable from storage: it holds no buffer, and
        # a context that staged nothing sees nothing.
        assert not hasattr(storage, "_pending")
        assert storage.new_context().staged_for((generators[0].id, "Component")) == {}
        assert not system.has_time_series(generators[0], name="load")

    assert not txn.has_staged_data


def test_to_json_after_a_block_includes_its_time_series(tmp_path):
    system, generators = make_system(tmp_path / "store")
    gen = generators[0]
    path = tmp_path / "system.json"

    with system.time_series_transaction() as txn:
        txn.add_time_series(make_series(), gen)
    system.to_json(path)

    restored = SimpleSystem.from_json(path)
    actual = restored.get_time_series(restored.get_component_by_id(gen.id), name="load")
    np.testing.assert_array_equal(actual.data, np.arange(LENGTH, dtype=np.float64))


def test_to_json_inside_a_block_is_rejected(tmp_path):
    """Serializing mid-block raises rather than producing a copy of pending state.

    Copying the artifact closes and reopens it, which would discard the open
    transaction; and a durable copy of state that may still roll back is not a coherent
    thing to write. The caller is told to move the call out of the block.
    """
    system, generators = make_system(tmp_path / "store")
    gen = generators[0]
    path = tmp_path / "system.json"

    with pytest.raises(ISOperationNotAllowed, match="transaction is open"):
        with system.time_series_transaction() as txn:
            txn.add_time_series(make_series(), gen)
            system.to_json(path)


def test_reader_built_on_transaction_sees_staged_data(tmp_path):
    system, generators = make_system(tmp_path)

    with system.time_series_transaction() as txn:
        for i, gen in enumerate(generators):
            txn.add_time_series(make_series(offset=i * 100.0), gen)
        reader = txn.build_time_series_reader(RESOLUTION, name="load")
        values = reader.read(INITIAL_TIMESTAMP)
        assert values[generators[0].id] == 0.0
        assert values[generators[1].id] == 100.0
