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
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, request

from aafbrowser.core import aaf as aaf_walker
from aafbrowser.core import cfb as cfb_walker
from aafbrowser.core import chain as chain_mod
from aafbrowser.core import mob_index as mob_index_mod
from aafbrowser.core import operator as operator_mod
from aafbrowser.core import resolver as resolver_mod

from . import state as state_mod


_STATIC_DIR = Path(__file__).parent / "static"
_logger = logging.getLogger("aafbrowser.web")

# Cache-buster token. Recomputed each Python process startup so a
# freshly-launched .app is guaranteed to load fresh CSS/JS even if
# the embedded WebKit cache holds stale responses.
import time as _time
_BUST = str(int(_time.time()))


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(_STATIC_DIR),
        static_url_path="/static",
    )
    # Disable Flask's default 12-hour static-file caching. WKWebView
    # (the embedded browser inside the macOS .app) caches HTTP
    # responses aggressively, which means a freshly-built .app can
    # still serve stale CSS / JS to the user across launches if the
    # response carries a long Cache-Control max-age. Setting this to
    # 0 tells Flask to emit `Cache-Control: public, max-age=0`, which
    # forces the browser to revalidate on every request.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
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


def _class_name_for_auid(handle: Any, class_id_str: str | None) -> str | None:
    """
    Decode a CFB storage class_id to its AAF class name via the
    metadictionary. Returns None when not registered (e.g. private
    extensions). Caller must hold the state lock.
    """
    if not class_id_str:
        return None
    try:
        from aaf2.auid import AUID

        cd = handle.metadict.classdefs_by_auid.get(AUID(class_id_str))
        return cd.class_name if cd is not None else None
    except Exception:
        return None


def _decorate_cfb_tree(node: dict[str, Any], handle: Any) -> dict[str, Any]:
    """
    Walk the cfb_tree output and add a `class_name` field next to each
    `class_id`. Mutates and returns `node`.
    """
    if not isinstance(node, dict):
        return node
    if "class_id" in node:
        node["class_name"] = _class_name_for_auid(handle, node.get("class_id"))
    for key in ("storages", "streams"):
        children = node.get(key)
        if isinstance(children, list):
            for child in children:
                _decorate_cfb_tree(child, handle)
    return node


def _macos_choose_file(prompt: str = "Open AAF file") -> Optional[str]:
    """
    Show a native macOS file picker via osascript and return the chosen
    POSIX path, or None if the user cancelled.

    AppleScript's `choose file` returns a HFS-style alias that we
    convert to a POSIX path via `POSIX path of`. User cancellation
    produces exit 1 with `(-128)` in stderr — surface as a clean None
    so the frontend can distinguish cancel from other failures.

    Raises FileNotFoundError if osascript is not on PATH.
    Raises RuntimeError on unexpected non-cancel failures.
    """
    if shutil.which("osascript") is None:
        raise FileNotFoundError("osascript not found in PATH")

    script_lines = [
        f'set theFile to choose file with prompt "{prompt}"',
        "POSIX path of theFile",
    ]
    cmd = ["osascript"]
    for line in script_lines:
        cmd.extend(["-e", line])

    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        return result.stdout.strip() or None
    # User-cancel signature: exit 1 with "(-128)" somewhere in stderr.
    if "-128" in (result.stderr or ""):
        return None
    raise RuntimeError(
        f"osascript failed: rc={result.returncode} "
        f"stderr={(result.stderr or '').strip()!r}"
    )


def _shutdown_after(delay_seconds: float = 0.1) -> None:
    """
    Schedule a process exit on a timer so the calling /api/quit
    response can flush to the browser before the server dies. Closes
    the open AAF file under the state lock first.

    Module-level so tests can monkeypatch it without juggling threads.
    """
    def _do_shutdown():
        try:
            with state_mod.state_lock():
                state_mod.close_file()
        except Exception:
            pass
        os._exit(0)

    threading.Timer(delay_seconds, _do_shutdown).start()


def _is_same_origin(req: Any) -> bool:
    """
    True if the request's Origin header (browser-set) matches the
    server's own host:port, OR if no Origin is present (CLI tools and
    tests don't set one and we trust them).

    Browsers set Origin on POST. A malicious cross-origin page cannot
    spoof Origin, so this reliably blocks the "stray fetch from
    external.com" CSRF case for the /api/quit endpoint.
    """
    origin = req.headers.get("Origin", "")
    if not origin:
        return True
    parsed = urlparse(origin)
    return parsed.netloc == req.host


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

    @app.post("/api/pick_file")
    def api_pick_file():
        """
        Open a native macOS file chooser and return the chosen POSIX
        path. Frontend uses this in place of the paste-a-path dialog
        when available.

        Returns:
        - 200 {"path": "<posix>"} on success
        - 200 {"path": null} on user cancel (so the frontend can
          distinguish cancel from "platform unsupported")
        - 501 {"error": "not_implemented"} on non-darwin
        - 500 on unexpected osascript failure
        """
        if sys.platform != "darwin":
            return _err(501, "not_implemented",
                        "native picker only available on macOS")
        try:
            chosen = _macos_choose_file()
        except Exception as exc:
            return _internal(exc)
        return jsonify({"path": chosen})

    @app.get("/")
    def index():
        index_html = _STATIC_DIR / "index.html"
        if not index_html.is_file():
            return jsonify({"error": "frontend_not_built"}), 503
        # Inject a cache-busting query string into the static asset
        # URLs so each new build of the .app gets fresh CSS/JS even
        # if the embedded browser has cached the old responses.
        # Uses a per-process bust token (app start time) so even
        # within a single .app version, restarts pick up live edits.
        from flask import Response
        html = index_html.read_text()
        html = html.replace(
            'href="/static/styles.css"',
            f'href="/static/styles.css?v={_BUST}"',
        )
        html = html.replace(
            'src="/static/app.js"',
            f'src="/static/app.js?v={_BUST}"',
        )
        return Response(html, mimetype="text/html")

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

    @app.post("/api/quit")
    def api_quit():
        """
        Shut the server down cleanly. Used by the topbar Quit link and
        the browser's beforeunload sendBeacon (so closing the tab
        kills the server, which is the actual common case for a
        single-user local app).

        Same-origin guarded: only honors requests from our own page,
        not stray cross-origin fetches.
        """
        if not _is_same_origin(request):
            return _err(403, "forbidden", "cross-origin quit blocked")
        # Schedule the actual exit on a timer so the response flushes
        # before the process dies. Module-level helper makes it easy to
        # monkeypatch in tests.
        _shutdown_after()
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
        cls_filter = request.args.get("class") or None
        name_contains = request.args.get("name_contains") or None
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            entries = list(state_mod._state.mob_index)  # snapshot under lock
            sha = state_mod._state.sha256
        filtered = mob_index_mod.filter_entries(
            entries, name_contains=name_contains, mob_class=cls_filter,
        )
        return jsonify({
            "sha256": sha,
            "mobs": [e.to_dict() for e in filtered],
            "total": len(filtered),
        })

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

    @app.get("/api/cfb/tree")
    @_require_open
    def api_cfb_tree():
        include_metadict = request.args.get("include_metadict") in ("1", "true", "yes")
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                tree = cfb_walker.cfb_tree(handle, include_metadict=include_metadict)
                _decorate_cfb_tree(tree, handle)
            except Exception as exc:
                return _internal(exc)
        return jsonify({"sha256": sha, "tree": tree})

    @app.get("/api/cfb/stream")
    @_require_open
    def api_cfb_stream():
        path = request.args.get("path")
        if not path:
            return _err(400, "bad_request", "missing 'path'")
        try:
            offset = int(request.args.get("offset", "0"))
            length = int(request.args.get("length", str(cfb_walker.DEFAULT_HEX_PREVIEW)))
        except ValueError:
            return _err(400, "bad_request", "offset and length must be integers")
        if offset < 0 or length < 0:
            return _err(400, "bad_request", "offset and length must be non-negative")

        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                data, info = cfb_walker.read_stream_bytes(
                    handle, path, offset=offset, length=length
                )
            except FileNotFoundError as exc:
                return _err(404, "not_found", str(exc))
            except Exception as exc:
                return _internal(exc)

        rows = cfb_walker.format_hex_view(data, base_offset=info["offset"])
        return jsonify(
            {
                "sha256": sha,
                "path": info["path"],
                "byte_size": info["total_size"],
                "offset": info["offset"],
                "length": info["read_length"],
                "truncated": info["truncated"],
                "rows": [asdict(r) for r in rows],
            }
        )

    @app.get("/api/find")
    @_require_open
    def api_find():
        pattern = request.args.get("pattern")
        if not pattern:
            return _err(400, "bad_request", "missing 'pattern'")
        scope = request.args.get("in", "both")
        layer = request.args.get("layer", "both")
        if scope not in ("names", "values", "both"):
            return _err(400, "bad_request", "in must be names|values|both")
        if layer not in ("aaf", "cfb", "both"):
            return _err(400, "bad_request", "layer must be aaf|cfb|both")
        # Repeated ?class=Foo&class=Bar query params restrict the AAF-layer
        # walk to those Mob classes. Empty list = no filter.
        mob_class_set = set(request.args.getlist("class")) or None

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return _err(400, "bad_pattern", str(exc))

        in_names = scope in ("names", "both")
        in_values = scope in ("values", "both")

        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            results: list[dict[str, Any]] = []
            try:
                if layer in ("aaf", "both"):
                    for m in resolver_mod.find_in_aaf(
                        handle, regex,
                        in_names=in_names, in_values=in_values,
                        mob_class=mob_class_set,
                    ):
                        results.append(_match_to_dict(m))
                if layer in ("cfb", "both"):
                    for m in resolver_mod.find_in_cfb(handle, regex):
                        results.append(_match_to_dict(m))
            except Exception as exc:
                return _internal(exc)

        return jsonify({"sha256": sha, "matches": results, "total": len(results)})

    @app.get("/api/resolve")
    @_require_open
    def api_resolve():
        ref = request.args.get("ref")
        if not ref:
            return _err(400, "bad_request", "missing 'ref'")
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                target = resolver_mod.resolve_mob(handle, ref)
                if target is not None:
                    return jsonify(
                        {
                            "sha256": sha,
                            "kind": "mob",
                            "mob_id_or_path": str(target.mob_id),
                            "class": type(target).__name__,
                            "name": getattr(target, "name", None)
                            if isinstance(getattr(target, "name", None), str)
                            else None,
                        }
                    )
                # Fall through: try as a path
                try:
                    target = resolver_mod.resolve_path(handle, ref)
                except ValueError as exc:
                    return _err(404, "not_found", str(exc))
                nm = getattr(target, "name", None)
                mob_id = getattr(target, "mob_id", None)
                return jsonify(
                    {
                        "sha256": sha,
                        "kind": "mob" if mob_id is not None else "path",
                        "mob_id_or_path": str(mob_id) if mob_id is not None else ref,
                        "class": type(target).__name__,
                        "name": nm if isinstance(nm, str) else None,
                    }
                )
            except Exception as exc:
                return _internal(exc)


    @app.get("/api/walk")
    @_require_open
    def api_walk():
        mob_id = request.args.get("mob_id")
        path = request.args.get("path")
        slot_id_raw = request.args.get("slot_id")
        max_hops_raw = request.args.get("max_hops", "64")
        if not mob_id and not path:
            return _err(400, "bad_request", "provide mob_id or path")
        if mob_id and path:
            return _err(400, "bad_request", "provide mob_id OR path, not both")
        try:
            slot_id = int(slot_id_raw) if slot_id_raw is not None else None
            max_hops = int(max_hops_raw)
        except ValueError:
            return _err(400, "bad_request", "slot_id and max_hops must be integers")
        if max_hops < 1:
            return _err(400, "bad_request", "max_hops must be >= 1")

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
                    start = target
                else:
                    try:
                        start = resolver_mod.resolve_path(handle, path)
                    except ValueError as exc:
                        return _err(404, "not_found", str(exc))

                try:
                    hops = chain_mod.walk_chain(
                        handle, start, slot_id=slot_id, max_hops=max_hops
                    )
                except ValueError as exc:
                    return _err(404, "not_found", str(exc))
            except Exception as exc:
                return _internal(exc)

        start_mob_id = getattr(start, "mob_id", None)
        return jsonify({
            "sha256": sha,
            "start": {
                "class": type(start).__name__,
                "name": (
                    getattr(start, "name", None)
                    if isinstance(getattr(start, "name", None), str)
                    else None
                ),
                "mob_id": str(start_mob_id) if start_mob_id is not None else None,
                "slot_id": slot_id,
            },
            "hops": [h.to_dict() for h in hops],
        })


    @app.get("/api/tracks")
    @_require_open
    def api_tracks():
        """
        Operator-layer view of the file: ordered list of audio + video
        tracks on the topmost CompositionMob, plus timecode info so the
        frontend can render per-clip timeline positions in samples /
        seconds / TC. Empty tracks list (not 404) when the file has no
        CompositionMobs — the frontend renders an empty Tracks state
        with a hint pointing at the All Mobs tab.
        """
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                tracks = operator_mod.list_tracks(handle)
                comp = operator_mod.pick_topmost_composition(handle)
                comp_meta = None
                tc_dict = None
                if comp is not None:
                    nm = getattr(comp, "name", None)
                    comp_meta = {
                        "mob_id": str(getattr(comp, "mob_id", "")) or None,
                        "name": nm if isinstance(nm, str) else None,
                    }
                    tc = operator_mod._build_timecode_info(comp)
                    if tc is not None:
                        tc_dict = tc.to_dict()
            except Exception as exc:
                return _internal(exc)

        return jsonify({
            "sha256": sha,
            "topmost_composition": comp_meta,
            "timecode": tc_dict,
            "tracks": [t.to_dict() for t in tracks],
        })

    @app.get("/api/sources")
    @_require_open
    def api_sources():
        """
        Cross-track deduplicated source-mob inventory.

        First call walks every clip in the topmost composition to
        compute use_count + used_by per source mob. Subsequent calls
        return the cached result. Cleared on /api/close + /api/open.

        Cost: ~3-6s on a 6000-clip session (PWD_310). Frontend shows
        a spinner; subsequent visits are instant.
        """
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            cached = state_mod._state.source_inventory
            if cached is None:
                try:
                    inventory = operator_mod.source_inventory(handle)
                except Exception as exc:
                    return _internal(exc)
                cached = [e.to_dict() for e in inventory]
                state_mod._state.source_inventory = cached
        return jsonify({
            "sha256": sha,
            "sources": cached,
            "total": len(cached),
        })

    @app.get("/api/session")
    @_require_open
    def api_session():
        """
        Headline summary of the open file for the top info bar:
        composition name, track + clip + mob counts, timecode info,
        duration. Cheap to compute (no per-clip walks).
        """
        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            path = state_mod._state.path
            file_size = None
            if path:
                try:
                    file_size = os.path.getsize(path)
                except OSError:
                    file_size = None
            try:
                summary = operator_mod.session_summary(
                    handle, file_size_bytes=file_size,
                )
            except Exception as exc:
                return _internal(exc)
        return jsonify({
            "sha256": sha,
            "path": path,
            "session": summary.to_dict(),
        })

    @app.get("/api/track/clips")
    @_require_open
    def api_track_clips():
        """
        Clips on a single slot of the topmost CompositionMob. Each clip
        carries operator-summary fields (recovered mic identity from the
        chain walk, source mob name, terminal reason).
        """
        slot_raw = request.args.get("slot")
        if slot_raw is None:
            return _err(400, "bad_request", "missing 'slot'")
        try:
            slot_id = int(slot_raw)
        except ValueError:
            return _err(400, "bad_request", "slot must be an integer")

        with state_mod.state_lock():
            if not state_mod.is_open():
                return _err(409, "no_file_open")
            handle = state_mod._state.handle
            sha = state_mod._state.sha256
            try:
                clips = operator_mod.list_clips(handle, slot_id)
            except ValueError as exc:
                return _err(404, "not_found", str(exc))
            except Exception as exc:
                return _internal(exc)

        return jsonify({
            "sha256": sha,
            "slot_id": slot_id,
            "clips": [c.to_dict() for c in clips],
        })


def _match_to_dict(m) -> dict[str, Any]:
    return {
        "layer": m.layer,
        "path": m.path,
        "classname": m.classname,
        "field": m.field,
        "value": m.value,
        "where": m.where,
    }
