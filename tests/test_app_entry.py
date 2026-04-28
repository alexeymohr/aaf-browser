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


def test_main_uses_webview_when_available(monkeypatch, tmp_path):
    """When pywebview is importable, main() should dispatch through
    _run_with_webview rather than the browser fallback."""

    class FakeWebview:
        pass

    calls = []

    def fake_run_webview(webview, url):
        calls.append(("webview", url))

    def fake_run_browser(url, thread):
        calls.append(("browser", url))

    monkeypatch.setattr(_app_entry, "_try_import_webview",
                        lambda: FakeWebview())
    monkeypatch.setattr(_app_entry, "_run_with_webview", fake_run_webview)
    monkeypatch.setattr(_app_entry, "_run_with_browser", fake_run_browser)

    rc = _app_entry.main(["script.py"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "webview"
    assert calls[0][1].startswith("http://127.0.0.1:")


def test_main_falls_back_to_browser_when_webview_missing(monkeypatch):
    """When pywebview can't be imported, main() should call the
    browser fallback so dev/CLI environments without the mac-build
    extras still work."""
    calls = []

    def fake_run_browser(url, thread):
        calls.append(("browser", url))

    def fake_run_webview(webview, url):
        calls.append(("webview", url))

    monkeypatch.setattr(_app_entry, "_try_import_webview", lambda: None)
    monkeypatch.setattr(_app_entry, "_run_with_browser", fake_run_browser)
    monkeypatch.setattr(_app_entry, "_run_with_webview", fake_run_webview)

    rc = _app_entry.main(["script.py"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "browser"
