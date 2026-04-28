"""
Bundled-app entrypoint.

Same as `aafbrowser web`, minus all of Click — designed for PyInstaller
to import as the `.app` bundle's start script. Lives inside the package
so PyInstaller's import analysis follows the dependency graph
naturally; also runnable as `python -m aafbrowser._app_entry [path]`
during local development.

Behavior at runtime:
1. Pick a free ephemeral port on 127.0.0.1.
2. Start wsgiref in a daemon thread.
3. If pywebview is importable, open a native macOS window with
   WKWebView pointed at the local URL (the bundled .app path).
   webview.start() blocks until the window closes.
4. Otherwise (no pywebview installed — typical for `python -m
   aafbrowser._app_entry` in a dev env), fall back to opening the
   user's default browser and blocking on the server thread.
5. SIGTERM and SIGINT both trigger clean shutdown in the
   browser-fallback path.
6. Optional first arg: a path to an AAF file to pre-open (so the
   window lands on the file already loaded — used by .aaf file
   association declared in Info.plist).
"""
from __future__ import annotations

import signal
import socket
import sys
import threading
import webbrowser
from wsgiref.simple_server import make_server

from aafbrowser.web import state as state_mod
from aafbrowser.web.app import create_app


def pick_free_port(host: str = "127.0.0.1") -> int:
    """Bind to port 0, read the kernel-assigned port, release the socket."""
    s = socket.socket()
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def parse_argv(argv: list[str]) -> str | None:
    """First positional arg, if any, is the AAF path to pre-open."""
    args = [a for a in argv[1:] if not a.startswith("-")]
    return args[0] if args else None


def _preopen(path: str) -> None:
    try:
        with state_mod.state_lock():
            state_mod.open_file(path)
    except Exception as exc:
        print(f"warning: failed to pre-open {path!r}: {exc}", file=sys.stderr)


def _install_signal_handlers() -> None:
    """SIGTERM should behave like Ctrl-C so the main thread's join()
    returns and the finally block runs (browser-fallback path only)."""
    def _term(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)


def _try_import_webview():
    """Return the pywebview module if importable, else None.

    Bundle builds always have pywebview; the fallback browser path
    triggers for `python -m aafbrowser._app_entry` from a dev env
    that didn't install the mac-build extras.
    """
    try:
        import webview  # type: ignore
        return webview
    except Exception:
        return None


def _make_bridge(webview):
    """
    Build the pywebview JS bridge — methods become callable from JS as
    `window.pywebview.api.<method>()`. We expose a single hook so the
    frontend can ask for a real native NSOpenPanel instead of the
    osascript fallback when running in the bundled app.
    """

    class Bridge:
        def pick_file(self) -> str | None:
            """Native NSOpenPanel via pywebview's create_file_dialog.

            Returns the chosen POSIX path or None on cancel — matches
            the shape of `/api/pick_file`'s JSON response so the
            frontend treats both paths uniformly.
            """
            if not webview.windows:
                return None
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("AAF Files (*.aaf)", "All files (*.*)"),
                allow_multiple=False,
            )
            if not result:
                return None
            return str(result[0])

    return Bridge()


def _run_with_webview(webview, url: str) -> None:
    """Native window mode: pywebview's WKWebView pointed at the local URL.

    `webview.start()` blocks the main thread until the user closes
    the window (red close button or Cmd-Q from the menu bar). When it
    returns we fall through to the finally block in main() which
    shuts the server down and closes any open AAF file.

    The Bridge object is exposed to JS as `window.pywebview.api`; the
    frontend prefers it over the osascript-based /api/pick_file when
    available.
    """
    webview.create_window(
        title="AAF Browser",
        url=url,
        width=1200,
        height=800,
        resizable=True,
        text_select=True,
        js_api=_make_bridge(webview),
    )
    webview.start()


def _run_with_browser(url: str, server_thread: threading.Thread) -> None:
    """Fallback: open the user's default browser, block on the server."""
    print("Press Ctrl-C or close the browser tab to stop.", file=sys.stderr)
    try:
        webbrowser.open(url)
    except Exception:
        pass

    _install_signal_handlers()
    try:
        server_thread.join()
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    aaf_path = parse_argv(argv)
    if aaf_path:
        _preopen(aaf_path)

    port = pick_free_port()
    app = create_app()
    server = make_server("127.0.0.1", port, app)

    server_thread = threading.Thread(
        target=server.serve_forever, name="aafbrowser-wsgi", daemon=True
    )
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    webview = _try_import_webview()

    # Always log the URL so it's discoverable in Console.app when
    # running as a bundled .app — useful for debugging without
    # attaching a terminal.
    mode = "WKWebView" if webview is not None else "browser"
    print(f"aafbrowser running on {url} ({mode} mode)", file=sys.stderr)

    try:
        if webview is not None:
            _run_with_webview(webview, url)
        else:
            _run_with_browser(url, server_thread)
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            with state_mod.state_lock():
                state_mod.close_file()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
