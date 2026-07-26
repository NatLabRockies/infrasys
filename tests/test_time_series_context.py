"""Tests for the time series context as a transaction object."""

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


def test_staged_adds_are_visible_only_through_their_own_context(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.open_time_series_store() as context:
        system.add_time_series(make_series(), gen, context=context)
        # The staging context sees its own work.
        assert system.has_time_series(gen, name="load", context=context)
        # A call with no context runs on its own and sees committed state only.
        assert not system.has_time_series(gen, name="load")

    # After the block commits, the addition is visible to everyone.
    assert system.has_time_series(gen, name="load")


def test_second_context_does_not_see_another_contexts_staged_adds(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.open_time_series_store() as writer:
        system.add_time_series(make_series(), gen, context=writer)
        with system.open_time_series_store() as reader:
            assert not system.has_time_series(gen, name="load", context=reader)

    assert system.has_time_series(gen, name="load")


def test_concurrent_contexts_each_commit_their_own_adds(tmp_path):
    """Two open batches must not observe or disturb each other's work.

    The old storage-owned buffer could not express this: one ambient batch meant a flush
    from anywhere drained whichever batch happened to be open.
    """
    system, generators = make_system(tmp_path, count=2)
    first_gen, second_gen = generators

    first = system.time_series.storage.new_context()
    second = system.time_series.storage.new_context()

    system.add_time_series(make_series("first"), first_gen, context=first)
    system.add_time_series(make_series("second", offset=100.0), second_gen, context=second)

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

    with system.open_time_series_store() as outer:
        system.add_time_series(make_series("outer"), outer_gen, context=outer)
        with pytest.raises(RuntimeError):
            with system.open_time_series_store() as inner:
                system.add_time_series(make_series("inner"), inner_gen, context=inner)
                msg = "inner failed"
                raise RuntimeError(msg)
        # The outer block is still usable and still holds its own work.
        assert system.has_time_series(outer_gen, name="outer", context=outer)

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
        with system.open_time_series_store() as context:
            system.remove_time_series(gen, name="keep", context=context)
            assert not system.has_time_series(gen, name="keep", context=context)
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
        with system.open_time_series_store() as context:
            system.add_time_series(make_series(), gen, context=context)
            # Reading requires the array to be in the store, so this flushes the batch.
            actual = system.get_time_series(gen, name="load", context=context)
            assert actual.data[0] == 0.0
            msg = "fail the block after it was forced to write"
            raise RuntimeError(msg)

    assert not system.has_time_series(gen, name="load")
    assert system.time_series.storage.store.list_time_series() == []


def test_context_rejects_use_after_its_block_exits(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with system.open_time_series_store() as context:
        system.add_time_series(make_series(), gen, context=context)

    with pytest.raises(ISOperationNotAllowed):
        system.add_time_series(make_series("other"), gen, context=context)


def test_context_rejects_use_against_a_different_system(tmp_path):
    first_system, first_gens = make_system(tmp_path / "first")
    second_system, second_gens = make_system(tmp_path / "second")

    with first_system.open_time_series_store() as context:
        with pytest.raises(ISOperationNotAllowed):
            second_system.add_time_series(make_series(), second_gens[0], context=context)


def test_duplicate_staged_on_one_context_is_rejected(tmp_path):
    system, generators = make_system(tmp_path)
    gen = generators[0]

    with pytest.raises(ISAlreadyAttached):
        with system.open_time_series_store() as context:
            system.add_time_series(make_series(), gen, context=context)
            system.add_time_series(make_series(), gen, context=context)

    assert not system.has_time_series(gen, name="load")


def test_transient_context_commits_each_call(tmp_path):
    """An operation with no context behaves exactly as it did before contexts existed."""
    system, generators = make_system(tmp_path)
    gen = generators[0]

    system.add_time_series(make_series(), gen)
    assert system.has_time_series(gen, name="load")
    assert len(system.time_series.storage.store.list_time_series()) == 1


def test_storage_holds_no_batch_state(tmp_path):
    """The storage object must not track contexts or staged work."""
    system, generators = make_system(tmp_path)
    storage = system.time_series.storage

    with system.open_time_series_store() as context:
        system.add_time_series(make_series(), generators[0], context=context)
        assert context.has_staged_data
        # Nothing about the open batch is reachable from storage: it holds no buffer, and
        # a context that staged nothing sees nothing.
        assert not hasattr(storage, "_pending")
        assert storage.new_context().staged_for((generators[0].id, "Component")) == {}
        assert not system.has_time_series(generators[0], name="load")

    assert not context.has_staged_data


def test_to_json_after_a_block_includes_its_time_series(tmp_path):
    system, generators = make_system(tmp_path / "store")
    gen = generators[0]
    path = tmp_path / "system.json"

    with system.open_time_series_store() as context:
        system.add_time_series(make_series(), gen, context=context)
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
        with system.open_time_series_store() as context:
            system.add_time_series(make_series(), gen, context=context)
            system.to_json(path)


def test_reader_built_with_context_sees_staged_data(tmp_path):
    system, generators = make_system(tmp_path)

    with system.open_time_series_store() as context:
        for i, gen in enumerate(generators):
            system.add_time_series(make_series(offset=i * 100.0), gen, context=context)
        reader = system.build_time_series_reader(RESOLUTION, name="load", context=context)
        values = reader.read(INITIAL_TIMESTAMP)
        assert values[generators[0].id] == 0.0
        assert values[generators[1].id] == 100.0
