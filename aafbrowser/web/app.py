"""
Flask app for the AAF Browser web GUI.

Endpoints are defined under /api and registered in `_register_routes`.
All pyaaf2 access goes through `state.state_lock()`.

Step 1 builds only the app factory and a minimal health endpoint;
later steps register /open, /close, /file, /mobs, /object, /cfb/*,
/find, /resolve.
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify


_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(_STATIC_DIR),
        static_url_path="/static",
    )
    _register_routes(app)
    return app


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
