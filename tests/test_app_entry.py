"""Tests for aafbrowser._app_entry — the bundle entrypoint module."""
from __future__ import annotations

import socket

from aafbrowser import _app_entry


def test_pick_free_port_returns_usable_loopback_port():
    port = _app_entry.pick_free_port()
    assert isinstance(port, int)
    assert 1024 < port < 65536
    # Confirm it's actually free: bind to it ourselves.
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_parse_argv_returns_none_when_no_positional():
    # argv[0] is the script path, argv[1:] is what the user passed
    assert _app_entry.parse_argv(["/path/to/_app_entry.py"]) is None


def test_parse_argv_returns_first_positional():
    argv = ["/path/to/_app_entry.py", "/path/to/file.aaf"]
    assert _app_entry.parse_argv(argv) == "/path/to/file.aaf"


def test_parse_argv_skips_flags():
    argv = ["script.py", "--verbose", "/file.aaf", "--debug"]
    assert _app_entry.parse_argv(argv) == "/file.aaf"


def test_module_is_importable():
    """Smoke check — PyInstaller will need to import this module
    without side effects at module load."""
    assert hasattr(_app_entry, "main")
    assert callable(_app_entry.main)
