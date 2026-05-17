"""
Process-global state for the web layer.

pyaaf2 is NOT thread-safe (no internal locks; verified). All access to the
open AAF file goes through a single `threading.Lock()` guarding this
module's globals. Callers in `aafbrowser.web.app` MUST acquire the lock
via the `state_lock()` context manager before touching `state.handle`.

One file open at a time. Re-opening a file while another is open is
allowed: the old handle is closed first.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from aafbrowser.core import mob_index as mob_index_mod
from aafbrowser.core import source as source_mod


_LOCK = threading.Lock()


@dataclass
class _State:
    handle: Optional[Any] = None  # the open aaf2 file (CompoundFileBinary container)
    path: Optional[str] = None  # absolute path string
    sha256: Optional[str] = None  # hash of file contents at open time
    # Eager Mob index: built once at /open and reused for /mobs filtering.
    mob_index: list[mob_index_mod.MobIndexEntry] = field(default_factory=list)
    # Source inventory: built lazily on first /api/sources request
    # (since it walks every clip in the topmost composition). None
    # until built. Cleared on close.
    source_inventory: Optional[list[dict[str, Any]]] = None


_state = _State()


@contextmanager
def state_lock() -> Iterator[_State]:
    """
    Acquire the process-global lock and yield the state object.

    Every endpoint handler that touches pyaaf2 (open file, walk content,
    walk CFB, read streams, run find) must use this context manager.
    """
    with _LOCK:
        yield _state


def open_file(path: str) -> dict[str, Any]:
    """
    Open `path` read-only, build the eager Mob index, and store both in
    process-global state. Closes any previously open file first.

    Returns a dict suitable for the `/open` JSON response.
    Caller must hold the state lock.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    abs_path = str(p.resolve())

    # Close any prior open file before swapping
    _close_locked()

    sha = source_mod.hash_file(abs_path)
    handle = source_mod.open_readonly(abs_path)

    _state.handle = handle
    _state.path = abs_path
    _state.sha256 = sha
    _state.mob_index = mob_index_mod.list_mobs(handle)

    classes_summary: dict[str, int] = {}
    for entry in _state.mob_index:
        classes_summary[entry.mob_class] = classes_summary.get(entry.mob_class, 0) + 1

    return {
        "path": _state.path,
        "sha256": _state.sha256,
        "mob_count": len(_state.mob_index),
        "classes_summary": classes_summary,
    }


def _close_locked() -> None:
    """Close the current handle if any. Caller must hold the lock."""
    if _state.handle is not None:
        try:
            _state.handle.close()
        except Exception:
            pass
    _state.handle = None
    _state.path = None
    _state.sha256 = None
    _state.mob_index = []
    _state.source_inventory = None


def close_file() -> None:
    """Close the current file. Caller must hold the state lock."""
    _close_locked()


def is_open() -> bool:
    """True if a file is currently open. Caller must hold the lock."""
    return _state.handle is not None


def file_metadata() -> Optional[dict[str, Any]]:
    """Current file metadata or None. Caller must hold the lock."""
    if _state.handle is None:
        return None
    classes_summary: dict[str, int] = {}
    for entry in _state.mob_index:
        classes_summary[entry.mob_class] = classes_summary.get(entry.mob_class, 0) + 1
    return {
        "path": _state.path,
        "sha256": _state.sha256,
        "mob_count": len(_state.mob_index),
        "classes_summary": classes_summary,
    }
