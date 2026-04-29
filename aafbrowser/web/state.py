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

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import aaf2


_LOCK = threading.Lock()


@dataclass
class _State:
    handle: Optional[Any] = None  # the open aaf2 file (CompoundFileBinary container)
    path: Optional[str] = None  # absolute path string
    sha256: Optional[str] = None  # hash of file contents at open time
    # Eager Mob index: built once at /open and reused for /mobs filtering.
    # Each entry: {"mob_id": str, "class": str, "name": str | None,
    #              "slot_count": int}
    mob_index: list[dict[str, Any]] = field(default_factory=list)
    # Phase 7 source inventory: built lazily on first /api/sources request
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


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_mob_index(handle: Any) -> list[dict[str, Any]]:
    """
    Build the eager Mob index at open time.

    Iterates `f.content.mobs` once and captures only identifying
    information — name, class, mob_id, slot_count. Property values are
    NOT serialized here; that's the lazy `/object` endpoint's job.
    """
    out: list[dict[str, Any]] = []
    for mob in handle.content.mobs:
        slots = getattr(mob, "slots", None)
        try:
            slot_count = len(list(slots)) if slots is not None else 0
        except Exception:
            slot_count = 0
        nm = getattr(mob, "name", None)
        out.append(
            {
                "mob_id": str(mob.mob_id),
                "class": type(mob).__name__,
                "name": nm if isinstance(nm, str) else None,
                "slot_count": slot_count,
            }
        )
    return out


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

    sha = _file_sha256(abs_path)
    handle = aaf2.open(abs_path, "r")
    if handle.writeable or handle.mode != "rb":
        handle.close()
        raise RuntimeError(
            f"refusing to proceed: pyaaf2 returned writeable handle "
            f"(mode={handle.mode!r}, writeable={handle.writeable!r})"
        )

    _state.handle = handle
    _state.path = abs_path
    _state.sha256 = sha
    _state.mob_index = _build_mob_index(handle)

    classes_summary: dict[str, int] = {}
    for entry in _state.mob_index:
        classes_summary[entry["class"]] = classes_summary.get(entry["class"], 0) + 1

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
        classes_summary[entry["class"]] = classes_summary.get(entry["class"], 0) + 1
    return {
        "path": _state.path,
        "sha256": _state.sha256,
        "mob_count": len(_state.mob_index),
        "classes_summary": classes_summary,
    }
