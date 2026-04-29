"""
Read-only invariant: every CLI command must leave its input file
byte-identical. We hash a per-test copy before and after each command
and assert the hashes match.

This is the third level of defense (after pyaaf2's `'r'` mode and the
absence of write paths in our code).
"""
from __future__ import annotations

import hashlib

import aaf2
from click.testing import CliRunner

from aafbrowser.cli.__main__ import cli


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream_path_with_some_bytes(aaf_path: str) -> str:
    with aaf2.open(aaf_path, "r") as f:
        for _, _, streams in f.cfb.walk():
            for st in streams:
                if st.byte_size >= 8:
                    return st.path()
    raise AssertionError("no stream of size >= 8 in fixture")


def _mob_urn(aaf_path: str) -> str:
    with aaf2.open(aaf_path, "r") as f:
        return str(next(iter(f.content.mobs)).mob_id)


def _commands_for(aaf_path: str) -> list[list[str]]:
    urn = _mob_urn(aaf_path)
    sp = _stream_path_with_some_bytes(aaf_path)
    return [
        ["tree", aaf_path],
        ["dump", aaf_path],
        ["dump", aaf_path, "--json"],
        ["dump", aaf_path, "--object-only"],
        ["dump", aaf_path, "--cfb-only"],
        ["dump", aaf_path, "--full-bytes", "--json"],
        ["cfb", aaf_path],
        ["cfb", aaf_path, "--include-metadict"],
        ["cfb", aaf_path, "--show-bytes", sp, "--length", "16"],
        ["cfb", aaf_path, "--json"],
        ["inspect", aaf_path, "--mob-id", urn],
        ["inspect", aaf_path, "--mob-id", urn, "--json"],
        ["inspect", aaf_path, "--path", f"Mobs/{urn}/Slots/0/Segment"],
        ["find", aaf_path, "--pattern", "(?i)mob"],
        ["find", aaf_path, "--pattern", "(?i)mob", "--layer", "aaf"],
        ["find", aaf_path, "--pattern", "(?i)mob", "--layer", "cfb"],
        ["find", aaf_path, "--pattern", "(?i)mob", "--in", "names"],
        ["find", aaf_path, "--pattern", "(?i)mob", "--in", "values"],
        ["find", aaf_path, "--pattern", "(?i)mob", "--json"],
        # Phase 9: operator-layer CLI commands.
        ["session", aaf_path],
        ["session", aaf_path, "--json"],
        ["tracks", aaf_path],
        ["tracks", aaf_path, "--json"],
        # minimal_aaf has no CompositionMob so 'clips' raises; skip
        # the slot-required command for the per-command loop. The
        # sources command works on any file.
        ["sources", aaf_path],
        ["sources", aaf_path, "--json"],
        ["sources", aaf_path, "--unused", "--json"],
    ]


def test_every_command_leaves_input_byte_identical(minimal_aaf_copy):
    """
    minimal_aaf_copy is a fresh per-test copy. Run every CLI command
    against it and assert the SHA-256 never changes.
    """
    aaf_path = str(minimal_aaf_copy)
    initial = _sha256(aaf_path)
    runner = CliRunner()

    for argv in _commands_for(aaf_path):
        result = runner.invoke(cli, argv, catch_exceptions=False)
        assert result.exit_code == 0, (argv, result.output)
        post = _sha256(aaf_path)
        assert post == initial, f"file mutated after running: {argv}"


def test_open_readonly_handle_is_not_writeable(minimal_aaf):
    """Direct sanity check on the underlying invariant."""
    with aaf2.open(str(minimal_aaf), "r") as f:
        assert f.mode == "rb"
        assert f.writeable is False
