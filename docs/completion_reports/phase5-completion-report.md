# AAF Browser — Phase 5 Completion Report

Date: 2026-04-27
Branch: main (8 Phase-5 commits, on top of the Phase 1-4 history)
Test suite: **181 tests, all passing** (`pytest` exits 0, ~6.7 s).
Python: 3.14.3 / pyaaf2 1.7.1 / click 8.3.2 / flask 3.1.3 /
pyinstaller 6.19.0 / Pillow 12.2.0 / **pywebview 6.2.1** /
pyobjc-core 12.1.

## What was built

Phase 5 takes the user from "double-click the Mac app and Safari pops
open" to "double-click the Mac app and a real Mac window appears."
The Flask server, the JS frontend, the chain-walk machinery — all
unchanged. What changed is the *shell* around them: a native
WKWebView via pywebview now hosts the GUI in an honest-to-goodness
app window with a title bar, dock icon, and standard macOS menu bar.

```
aafbrowser/_app_entry.py            # main() now dispatches to
                                    # _run_with_webview (WKWebView)
                                    # or _run_with_browser (fallback)
aafbrowser/web/static/app.js        # Open prefers
                                    # window.pywebview.api.pick_file
                                    # over /api/pick_file when the
                                    # JS bridge is present;
                                    # syncWindowTitle() mirrors the
                                    # open-file basename to
                                    # document.title
packaging/macos/aafbrowser.spec     # collect_submodules('webview',
                                    # 'objc', 'Foundation', 'AppKit',
                                    # 'WebKit') + collect_data_files
                                    # ('webview')
pyproject.toml                      # mac-build extras gain
                                    # pywebview>=6.2,<7
```

The CLI distribution is **unchanged**: `aafbrowser web` from a pip
install still opens the user's default browser. The pywebview shell
is bundle-only.

### Acceptance-criteria checklist

| # | Criterion | Status |
|---|-----------|--------|
| 1 | The bundle launches into a native macOS window — no Safari, no Chrome. Window has the standard close/min/zoom buttons. | OK (smoke-tested, real WKWebView opens) |
| 2 | Cmd-Q from the menu bar quits the app cleanly. Closing the window via the red close button does the same. State teardown runs in both cases. | OK (webview.start() returns when window closes; finally block runs server.shutdown + state.close_file) |
| 3 | Clicking Open in the topbar shows a native file chooser with `.aaf` filtering via pywebview's create_file_dialog. | OK (Bridge.pick_file wired; window.pywebview.api detected by frontend) |
| 4 | Inside the bundled app, walking the Phase-3 reference clip returns `PW_310_ISO1_B PTN=1`. | OK — see "Phase-3 cross-check" below |
| 5 | `aafbrowser web` from a pip install is unchanged. | OK (browser-fallback path retained, /api/pick_file still works) |
| 6 | Bundle launches under hardened runtime + entitlements. | OK (existing entitlements from Phase 4 cover WKWebView's needs; no new entitlement required) |
| 7 | `pytest` exits 0. | OK (181 passed; 2 added in Phase 5) |
| 8 | Final .dmg under 60 MB. | OK (14 MB, well under estimate) |

### Test breakdown (Phase 5 additions)

- `tests/test_app_entry.py` — 2 new dispatch tests verifying that
  `main()` calls `_run_with_webview` when pywebview is importable and
  falls back to `_run_with_browser` when not.

Phase 1+2+3+4's 179 tests still pass unchanged.

## Deviations from the brief

1. **No new entitlements needed.** The brief flagged WKWebView might
   need additional entitlements on top of Phase 4's hardened-runtime
   set (e.g. JIT for JavaScriptCore, network client). It didn't —
   `allow-unsigned-executable-memory` + `disable-library-validation`
   + `network.server` cover what WKWebView wants on macOS Tahoe.
2. **Bundle size delta much smaller than estimated.** The brief
   flagged ~30-40 MB additional. Actual delta: **+4 MB on the .app,
   +2 MB on the .dmg** (pywebview binds against the *system*
   AppKit/WebKit/Foundation frameworks; only the Python wrapper
   modules land in the bundle, not the framework binaries). Net
   .dmg goes from 12 MB → 14 MB.
3. **Cold-start delta as expected.** Phase 4: 0.36 s. Phase 5:
   ~1.0 s. The extra ~700 ms is WKWebView initialization on the
   main thread before `webview.start()` returns control. Still
   subjectively snappy.

## Phase-3 cross-check inside the new bundle

The crucial test: pyaaf2's dynamic class registration + WKWebView +
Flask + the chain-walk machinery all coexist in one bundle. Verified
by running the same MasterMob walk that Phases 3 and 4 nailed:

```
$ "dist/AAF Browser.app/Contents/MacOS/AAF Browser" \
      samples/PWD_310_LC_10-07-2025.aaf

# Native window opens. Server logs in stderr / Console.app:
$ curl -s http://127.0.0.1:<port>/api/walk?mob_id=urn:smpte:umid:060a2b34.01010101.01010f00.13000000.060e2b34.7f7f2a80.68e54a00.b62cad52

[0] MasterMob 'PW VO 310 B Round 1.wav.new.05' slot=1 ptn=1
[1] SourceMob ''                                slot=1 ptn=1
[2] SourceMob 'PW VO 310 B Round 1.wav'         slot=1 ptn=1   ★ no_source_id
```

Identical output to Phases 3 and 4 (pip install + Phase-4 bundle).
Bundling pywebview alongside pyaaf2 didn't break either's dynamic
imports.

## Performance + size numbers

| Measurement | Phase 4 | Phase 5 |
|---|---|---|
| `.app` bundle size | 25 MB | **29 MB** |
| `.dmg` size | 12 MB | **14 MB** |
| Cold-start time (launch → first GET landing) | 0.36 s | **~1.06 s** |
| Test suite | 179 tests | **181 tests** |
| Test suite runtime | 7.3 s | 6.7 s (some variance) |

Cold start is dominated by WKWebView setup on the main thread before
`webview.start()` returns. After that, all subsequent navigation
(clicking Mob rows, walking chains, opening files via the native
picker) runs at the same speed as the pip install — the only
difference is one extra in-process IPC hop for the `pywebview.api`
calls and one extra layer (WKWebView itself) between the JS and the
Flask server.

## GUI ergonomics — what surfaced in real use

1. **The native window changes the feel completely.** The Phase-4
   "Mac app" was a launcher that delegated to Safari and felt like a
   bug. The Phase-5 window with the standard macOS chrome (red/yellow/
   green buttons, dock icon, App-name menu) feels like a legitimate
   small Mac app. Cmd-Q does what Cmd-Q is supposed to do.
2. **Default macOS menu bar is enough for v1.** pywebview installs
   the standard AAF Browser / File / Edit / View / Window / Help
   menu structure for free. We didn't need to customize anything.
   For Phase 6 we'd add: File → Open (Cmd-O) wired to the picker,
   File → Recent Files, View → Reload, etc.
3. **NSOpenPanel beats the osascript chooser.** With pywebview's
   `create_file_dialog`, the picker has proper `.aaf` extension
   filtering ("AAF Files (*.aaf)") and behaves like every other
   native macOS open dialog. The osascript path was usable but
   slightly off-feel.
4. **Window title shows the open file.** After loading
   `PWD_310_LC_10-07-2025.aaf`, the title bar reads
   `AAF Browser — PWD_310_LC_10-07-2025.aaf`. Same hook (set
   `document.title`) works in browser-mode use too — tab title
   updates identically.
5. **One thing missing**: dragging an .aaf onto the running app's
   dock icon doesn't open it. Same Phase-4 limitation — needs an
   in-process AppKit `application:openFile:` delegate handler. Cold-
   start argv path still works.

## Risks that didn't materialize (the brief's worry list)

- **PyObjC + Python 3.14 compatibility** — installed cleanly, no
  fixes needed.
- **PyInstaller bundling pywebview's WebKit binding** — one round of
  smoke-test was enough; `collect_submodules` covered it.
- **Hardened runtime + WKWebView entitlements** — the existing set
  was sufficient.
- **`webview.start()` single-window-on-main-thread** — Flask runs
  in a daemon thread (already did), pywebview gets the main thread,
  no contention.
- **Bundle size growth** — much less than estimated.

## Recommendations for a possible Phase 6

No commitment, but a few things came up:

1. **In-process Apple-event handling** for `.aaf` files dropped onto
   a running app. Requires either pywebview's
   `events.app_open_file` (added in some recent version — verify)
   or a small PyObjC `NSApplicationDelegate` override. Removes the
   Phase-4 cold-start-only limitation.
2. **Custom menu bar items.** File → Open (Cmd-O), File → Recent
   Files, View → Reload, File → Close (Cmd-W with the existing
   close-handler). pywebview supports menu customization on macOS
   via `webview.menu`.
3. **Drop the HTTP layer in favor of a direct JS bridge.** With the
   Bridge already in place, expanding it to expose the full
   `/api/*` surface as Python methods would let us shut down the
   wsgiref server entirely. Smaller bundle, fewer moving parts. The
   `/api/*` routes stay as the public interface for the
   `aafbrowser web` CLI mode. Probably a 1-2 day refactor.
4. **Code signing on personal builds.** With Developer ID
   provisioned, document the local-build flow that produces a
   signed-and-notarized .dmg without needing CI.
5. **Real logo** — placeholder still in place. Cheap to swap.

## Build sequence (commits)

8 Phase-5 commits:

1. Phase 5 step 0: brief
2. Phase 5 step 1: pywebview added to mac-build extras
3. Phase 5 step 2: _app_entry.py uses pywebview when importable
4. Phase 5 step 3: PyInstaller spec bundles pywebview + objc + WebKit
5. Phase 5 step 4: pywebview JS bridge for native NSOpenPanel
6. Phase 5 step 5: window title sync to current filename
7. Phase 5 step 6: README + PROJECT_OVERVIEW updates
8. Phase 5 step 7: completion report (this file)
