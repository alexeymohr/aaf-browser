"""
One seam for opening AAF files read-only and hashing them.

This module owns CLAUDE.md invariant #1 (read-only): every adapter (CLI,
web, future MCP server) goes through `open_readonly` and `hash_file`
instead of re-implementing them. Centralising the open prevents drift
in the writeable-mode check; centralising the hash prevents drift in
the chunk size or algorithm used for the read-only verification.
"""
from __future__ import annotations

import hashlib
from typing import Any

import aaf2


_HASH_CHUNK = 1 << 16  # 64 KiB; tuned for sequential SSD reads


def hash_file(path: str) -> str:
    """Return the SHA-256 hex digest of the file at `path`."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def open_readonly(path: str) -> Any:
    """Open an AAF file read-only and refuse to proceed if the resulting
    handle is writeable (defence in depth — pyaaf2's mode='r' has always
    been honoured in practice but we never want to find out otherwise).

    Returns the raw pyaaf2 file handle, which supports the
    context-manager protocol so callers can use `with open_readonly(p) as f:`.

    Raises RuntimeError if the handle reports writeable=True or a mode
    other than 'rb'.
    """
    handle = aaf2.open(path, "r")
    if handle.writeable or handle.mode != "rb":
        try:
            handle.close()
        finally:
            raise RuntimeError(
                f"refusing to proceed: pyaaf2 returned writeable handle "
                f"(mode={handle.mode!r}, writeable={handle.writeable!r})"
            )
    return handle
