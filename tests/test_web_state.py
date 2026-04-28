"""Tests for aafbrowser.web.state — process-global open file + lock."""
from __future__ import annotations

import threading

import pytest

from aafbrowser.web import state as state_mod


@pytest.fixture(autouse=True)
def _close_after_each():
    """Ensure the global state is always closed between tests."""
    yield
    with state_mod.state_lock():
        state_mod.close_file()


def test_open_builds_mob_index_and_classes_summary(two_mob_aaf):
    with state_mod.state_lock():
        meta = state_mod.open_file(str(two_mob_aaf))
    assert meta["path"].endswith("two_mob.aaf")
    assert meta["mob_count"] == 2
    assert meta["classes_summary"].get("MasterMob") == 2
    assert isinstance(meta["sha256"], str) and len(meta["sha256"]) == 64


def test_is_open_and_file_metadata(two_mob_aaf):
    with state_mod.state_lock() as s:
        assert state_mod.is_open() is False
        state_mod.open_file(str(two_mob_aaf))
        assert state_mod.is_open() is True
        meta = state_mod.file_metadata()
        assert meta is not None and meta["mob_count"] == 2


def test_close_resets_state(two_mob_aaf):
    with state_mod.state_lock() as s:
        state_mod.open_file(str(two_mob_aaf))
        state_mod.close_file()
        assert state_mod.is_open() is False
        assert state_mod.file_metadata() is None
        assert s.path is None
        assert s.handle is None
        assert s.mob_index == []


def test_open_replaces_previous_file(two_mob_aaf, minimal_aaf):
    with state_mod.state_lock():
        state_mod.open_file(str(two_mob_aaf))
        meta2 = state_mod.open_file(str(minimal_aaf))
    assert meta2["path"].endswith("minimal.aaf")
    assert meta2["mob_count"] == 1


def test_open_unknown_path_raises():
    with state_mod.state_lock():
        with pytest.raises(FileNotFoundError):
            state_mod.open_file("/no/such/path.aaf")


def test_state_lock_serializes_concurrent_calls(two_mob_aaf):
    """
    Two threads calling open_file under the lock must serialize. We
    detect serialization by sampling `is_open()` while holding the lock
    and asserting we never observe the other thread's mid-state.
    """
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def runner(path: str):
        try:
            barrier.wait()
            with state_mod.state_lock():
                state_mod.open_file(path)
                # While we hold the lock, state must be consistent
                if not state_mod.is_open():
                    errors.append("inconsistent state under lock")
        except Exception as e:
            errors.append(repr(e))

    t1 = threading.Thread(target=runner, args=(str(two_mob_aaf),))
    t2 = threading.Thread(target=runner, args=(str(two_mob_aaf),))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert errors == []
