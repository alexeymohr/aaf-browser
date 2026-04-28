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

from aafbrowser.core import aaf as aaf_walker
from aafbrowser.core import resolver as resolver_mod

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

    @app.get("/api/mobs")
    @_require_open
    def api_mobs():
        cls_filter = request.args.get("class")
        name_contains = request.args.get("name_contains") or ""
        name_contains_lc = name_contains.lower()
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            entries = list(state_mod._state.mob_index)  # snapshot under lock
            sha = state_mod._state.sha256
        out = []
        for e in entries:
            if cls_filter and e["class"] != cls_filter:
                continue
            if name_contains:
                nm = e["name"] or ""
                if name_contains_lc not in nm.lower():
                    continue
            out.append(e)
        return jsonify({"sha256": sha, "mobs": out, "total": len(out)})

    @app.get("/api/object")
    @_require_open
    def api_object():
        mob_id = request.args.get("mob_id")
        path = request.args.get("path")
        if not mob_id and not path:
            return _err(400, "bad_request", "provide mob_id or path")
        if mob_id and path:
            return _err(400, "bad_request", "provide mob_id OR path, not both")

        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                if mob_id:
                    target = resolver_mod.resolve_mob(handle, mob_id)
                    if target is None:
                        return _err(404, "not_found", f"mob_id={mob_id!r}")
                else:
                    try:
                        target = resolver_mod.resolve_path(handle, path)
                    except ValueError as exc:
                        return _err(404, "not_found", str(exc))
                if hasattr(target, "properties"):
                    serial = aaf_walker.serialize_object(target)
                else:
                    from aafbrowser.core.serialize import serialize_scalar

                    serial = {
                        "_type": "scalar_leaf",
                        "value": serialize_scalar(target),
                    }
            except Exception as exc:
                return _internal(exc)

        return jsonify({"sha256": sha, "object": serial})
