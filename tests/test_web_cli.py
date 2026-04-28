"""Smoke tests for `aafbrowser web` CLI subcommand."""
from __future__ import annotations

import socket
import threading
import urllib.request
from wsgiref.simple_server import make_server

from click.testing import CliRunner

from aafbrowser.cli.__main__ import cli
from aafbrowser.web.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_web_help_renders():
    runner = CliRunner()
    r = runner.invoke(cli, ["web", "--help"])
    assert r.exit_code == 0
    assert "Start the local web GUI" in r.output


def test_web_app_serves_one_request_via_wsgiref():
    """
    The CLI subcommand uses wsgiref.simple_server. We exercise the same
    server here on one request so this test mirrors what `aafbrowser web`
    actually runs without paying the cost of serve_forever / shutdown.
    """
    port = _free_port()
    app = create_app()
    server = make_server("127.0.0.1", port, app)

    def serve_one():
        server.handle_request()

    t = threading.Thread(target=serve_one, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=2
        ) as resp:
            body = resp.read()
        assert b'"ok":true' in body.replace(b" ", b"")
    finally:
        t.join(timeout=2)
        server.server_close()
