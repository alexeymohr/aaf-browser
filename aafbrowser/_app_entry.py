"""
Bundled-app entrypoint.

Same as `aafbrowser web`, minus all of Click — designed for PyInstaller
to import as the `.app` bundle's start script. Lives inside the package
so PyInstaller's import analysis follows the dependency graph
naturally; also runnable as `python -m aafbrowser._app_entry [path]`
during local development.

Behavior:
1. Pick a free ephemeral port on 127.0.0.1.
2. Start wsgiref in a daemon thread.
3. Open the user's default browser at the chosen URL.
4. Block on the server thread; SIGTERM (Cmd-Q from Dock) and SIGINT
   trigger clean shutdown.
5. Optional first arg: a path to an AAF file to pre-open (so the
   browser tab lands on the file already loaded — used when launching
   from Finder via the .aaf file association declared in Info.plist).
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
    returns and the finally block runs."""
    def _term(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)


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
    print(f"aafbrowser running on {url}", file=sys.stderr)
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
