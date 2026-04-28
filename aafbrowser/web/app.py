"""
Flask app for the AAF Browser web GUI.

All endpoints are mounted under /api. Every pyaaf2 access goes through
`state.state_lock()` because pyaaf2 is not thread-safe.

Errors follow the brief's contract:
- 409 with `{"error": "no_file_open"}` when no file is open
- 404 with `{"error": "not_found", "detail": ...}` for missing mob/path
- 400 with `{"error": "bad_pattern", "detail": ...}` for bad regex
- 500 with `{"error": "internal", "detail": <msg>}` for unexpected pyaaf2
   errors; never expose the stack trace to the browser
"""
from __future__ import annotations

import logging
import traceback
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import Flask, jsonify, request

from . import state as state_mod


_STATIC_DIR = Path(__file__).parent / "static"
_logger = logging.getLogger("aafbrowser.web")


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(_STATIC_DIR),
        static_url_path="/static",
    )
    _register_routes(app)
    return app


# --- helpers ---------------------------------------------------------------


def _err(code: int, error: str, detail: Any = None):
    payload: dict[str, Any] = {"error": error}
    if detail is not None:
        payload["detail"] = detail
    return jsonify(payload), code


def _internal(exc: Exception):
    _logger.exception("internal error")
    return _err(500, "internal", str(exc))


def _require_open(fn: Callable):
    """Decorator: 409 if no file is open. Caller acquires lock inside."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return _internal(exc)

    return wrapper


# --- routes ----------------------------------------------------------------


def _register_routes(app: Flask) -> None:

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        index_html = _STATIC_DIR / "index.html"
        if index_html.is_file():
            return app.send_static_file("index.html")
        return jsonify({"error": "frontend_not_built"}), 503

    @app.post("/api/open")
    @_require_open
    def api_open():
        body = request.get_json(silent=True) or {}
        path = body.get("path")
        if not isinstance(path, str) or not path:
            return _err(400, "bad_request", "missing 'path' string in body")
        with state_mod.state_lock():
            try:
                meta = state_mod.open_file(path)
            except FileNotFoundError as exc:
                return _err(404, "not_found", str(exc))
        return jsonify(meta)

    @app.post("/api/close")
    @_require_open
    def api_close():
        with state_mod.state_lock():
            state_mod.close_file()
        return jsonify({"ok": True})

    @app.get("/api/file")
    @_require_open
    def api_file():
        with state_mod.state_lock():
            meta = state_mod.file_metadata()
        if meta is None:
            return _err(404, "no_file_open")
        return jsonify(meta)
