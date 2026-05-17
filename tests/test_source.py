"""Tests for the core.source seam: read-only open + hash."""
from __future__ import annotations

import hashlib

from aafbrowser.core import source as source_mod


def test_hash_file_matches_stdlib_sha256(tmp_path):
    p = tmp_path / "blob.bin"
    payload = b"hello\x00world" * 1000
    p.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert source_mod.hash_file(str(p)) == expected


def test_open_readonly_returns_unwriteable_handle(minimal_aaf):
    handle = source_mod.open_readonly(str(minimal_aaf))
    try:
        assert handle.writeable is False
        assert handle.mode == "rb"
    finally:
        handle.close()


def test_open_readonly_supports_with_statement(minimal_aaf):
    """The returned handle must work as a context manager — every CLI
    command uses `with source.open_readonly(p) as f:`."""
    with source_mod.open_readonly(str(minimal_aaf)) as f:
        assert f.content is not None
