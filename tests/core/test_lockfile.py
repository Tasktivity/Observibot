from __future__ import annotations

import os
from pathlib import Path

import pytest

from observibot.core.monitor import (
    LockfileError,
    acquire_lockfile,
    release_lockfile,
)


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = tmp_path / "observibot.lock"
    acquire_lockfile(lock)
    assert lock.exists()
    assert int(lock.read_text()) == os.getpid()
    release_lockfile(lock)
    assert not lock.exists()


def test_stale_lockfile_is_cleaned_up(tmp_path: Path) -> None:
    lock = tmp_path / "observibot.lock"
    # Use PID 1 — almost always the init process, but definitely never a dead PID
    # That doesn't work for "stale" semantics, so use a PID that doesn't exist.
    # We pick a very large number unlikely to be a live PID.
    lock.write_text("9999999")
    acquire_lockfile(lock)
    assert int(lock.read_text()) == os.getpid()
    release_lockfile(lock)


def test_live_lockfile_blocks_acquire(tmp_path: Path) -> None:
    lock = tmp_path / "observibot.lock"
    # Current process is always alive.
    lock.write_text(str(os.getpid()))
    with pytest.raises(LockfileError):
        acquire_lockfile(lock)
    # Cleanup — remove the lockfile we wrote manually
    lock.unlink()


def test_corrupt_lockfile_is_cleaned_up(tmp_path: Path) -> None:
    lock = tmp_path / "observibot.lock"
    lock.write_text("not a pid")
    acquire_lockfile(lock)
    assert int(lock.read_text()) == os.getpid()
    release_lockfile(lock)
