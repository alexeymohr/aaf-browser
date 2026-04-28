# AAF Browser — Phase 5 Brief

## Goal

Make the macOS app feel like a real Mac app, not a launcher that
shoves you into Safari. Embed a native `WKWebView` so the existing web
GUI renders inside an honest-to-goodness app window with a Mac title
bar, dock icon, and menu bar — Cmd-Q quits the app, Cmd-W closes the
window, and the user never sees Safari/Chrome at all.

The existing Flask + vanilla-JS frontend stays unchanged: the
WKWebView loads `http://127.0.0.1:<port>/` from the same wsgiref
server we already ship. We're swapping the *shell* around the GUI,
not rewriting it.

Project context: read `docs/PROJECT_OVERVIEW.md`, repo-root
`CLAUDE.md`, and the Phase 1-4 completion reports under
`docs/completion_reports/`. This brief covers Phase 5 only.

## Why now

Phase 4 took the launch friction away ("download the .dmg, drag to
/Applications, double-click") but left a bigger UX gap: the user
clicks the app and a *separate Safari tab* opens, often behind their
current window. The Mac app has no window of its own, and the web
content sits inside a browser they didn't ask for. That's the
"letter, not spirit" of "macOS app" the user called out.

`pywebview` is the small tool that closes the gap. ~5 lines of Python
gives us a native `WKWebView` window pointed at the local server.
The .dmg gets bigger (~30-40 MB more, mostly PyObjC), but the
experience matches what people mean by "Mac app."

## Decisions to lock in

- **Architecture stays HTTP, not JS-bridge.** The bundled app keeps
  spinning up the wsgiref + Flask server; pywebview just renders
  `http://127.0.0.1:<port>/` inside a native window. Reuses
  everything Phases 1-4 built. A future phase could swap to a direct
  Python-from-JS bridge if the network round-trip starts mattering;
  for v1 it doesn't.
- **CLI `aafbrowser web` unchanged.** Still opens the user's default
  browser via `webbrowser.open`. The pywebview shell is bundle-only —
  triggered by `_app_entry.py`, never by the Click command. Keeps the
  dev / CLI / SSH-friendly paths working as before.
- **macOS-only.** pywebview supports Linux + Windows but Phase 5
  targets the same `arm64` Macs Phase 4 targets. Linux/Windows .app
  is out of scope.
- **Native file picker via pywebview's `create_file_dialog`** when
  the bundle is the host. Frontend detects `window.pywebview` and
  prefers it; falls back to `/api/pick_file` (osascript) otherwise.
- **Default macOS menu bar.** pywebview installs a standard
  AAF Browser / File / Edit / View / Window / Help menu structure
  for free. Phase 5 doesn't customize it. Polish in a later phase if
  needed.
- **In-process file association handling stays out.** "Open With" on
  a `.aaf` while the app is already running still requires a
  full AppKit delegate — pywebview doesn't expose this directly. The
  cold-start argv path from Phase 4 already covers the common case.
  Defer in-process Apple-event handling.

## Scope

In:

- `pywebview` added to `[project.optional-dependencies] mac-build`
  (pinned via `pywebview==6.2.1`, transitively pulls PyObjC).
- `aafbrowser/_app_entry.py` rewritten:
  - Start the Flask server in a daemon thread (already does).
  - Replace `webbrowser.open` + `server_thread.join` with
    `webview.create_window(...)` + `webview.start()`.
  - Window-close → server.shutdown() + state.close_file() + return.
  - Window title: `"AAF Browser"`, updated to
    `"AAF Browser — <basename>"` once a file is loaded (via the
    `loaded` event hooking into a small JS bridge that reports the
    current file path back to Python on /api/file changes).
- Optional pywebview frontend hook in `static/app.js`: detect
  `window.pywebview` global; if present, use
  `window.pywebview.api.pick_file()` (a Python-side hook we expose)
  instead of `fetch('/api/pick_file')`. Cleaner native dialog with
  proper UTI-filtering for `.aaf`.
- PyInstaller spec updates: `collect_submodules('webview')`,
  `collect_submodules('objc')`, `collect_data_files('webview')` to
  pull in the platform-specific webview backend code.
- Bundle smoke test: launch the .app, see a native window with the
  GUI inside, open the Phase-3 reference file via the native dialog,
  walk a chain, confirm the result matches `PW_310_ISO1_B PTN=1`.
- README updates: replace the "your default browser opens" language
  with "a native window opens"; remove the now-stale "close the tab
  to quit" paragraph in favor of "close the window or Cmd-Q".
- Phase 5 completion report.

Out:

- JS bridge replacement of HTTP (deferred to a possible Phase 6).
- Custom native menu bar items beyond pywebview's defaults.
- `NSApplicationOpenFile` (Apple-event-driven open-with for an
  already-running app).
- Linux / Windows packaging (still macOS-only).
- Sparkle / auto-update.
- A new icon design (placeholder PNG from Phase 4 stays for v1).
- Removing the existing `/api/pick_file` osascript path. CLI users
  who run `aafbrowser web` still benefit from it.

## Verified API surface (use this, don't guess)

`pywebview` 6.2.1 on macOS surfaces:

```python
import webview

window = webview.create_window(
    title="AAF Browser",
    url="http://127.0.0.1:<port>/",
    width=1200,
    height=800,
    resizable=True,
)
webview.start()    # blocks; returns when all windows close

# Native file dialog (real NSOpenPanel):
paths = window.create_file_dialog(
    dialog_type=webview.OPEN_DIALOG,
    file_types=("AAF Files (*.aaf)", "All files (*.*)"),
    allow_multiple=False,
)
# Returns a tuple of paths or None on cancel.

# JS-callable Python hooks:
class Bridge:
    def pick_file(self):
        return window.create_file_dialog(...)

webview.create_window(..., js_api=Bridge())
# Frontend calls await window.pywebview.api.pick_file()
```

The PyInstaller spec needs:

```python
hiddenimports += collect_submodules("webview") + collect_submodules("objc")
datas += collect_data_files("webview")
```

without these the bundle silently lacks the platform backend and
`webview.start()` raises at runtime.

## Build sequence

One commit per step.

1. **Add `pywebview` to mac-build extras**, install locally, write a
   2-line smoke script that opens a window with `https://example.com`
   and confirm the bundle layout still works. Verify the install
   actually pulls PyObjC (Mac-only deps).
2. **Refactor `_app_entry.py`** to use pywebview when importable,
   fall back to the existing `webbrowser.open + server_thread.join`
   when not (so `python -m aafbrowser._app_entry` from a dev env
   without pywebview keeps working). Handle window-close → cleanup.
   Smoke-test from `python -m aafbrowser._app_entry samples/...aaf`.
3. **Update the PyInstaller spec** to include webview + objc
   submodules and webview's data files. Rebuild the .app and verify
   it launches with a native window. Walk the Phase-3 reference
   chain to confirm pyaaf2 + WebKit + Flask all coexist in one
   bundle.
4. **JS bridge for the file picker.** Add `Bridge` class in
   `_app_entry.py` exposing a `pick_file()` method that uses
   `window.create_file_dialog`. Update `app.js` to detect
   `window.pywebview` and prefer that path; fall back to
   `/api/pick_file` otherwise. Keep `/api/pick_file` working —
   browser-mode users still need it.
5. **Window title sync.** When the user opens a file, update the
   window title to `"AAF Browser — <basename>"`. Use a small frontend
   helper that calls into the bridge with the current path; or do it
   purely in CSS via `document.title` + a window.title-sync hook on
   the Python side (pywebview reads `document.title` automatically
   in some configurations).
6. **README updates**: replace browser-launch language with
   native-window language; document the new minimum experience
   (double-click .app → window appears).
7. **Phase 5 completion report**.

## Acceptance criteria

1. `bash packaging/macos/build.sh` produces a bundle that launches
   into a native macOS window — no Safari, no Chrome, no browser tab.
   Window has the macOS standard close/minimize/zoom buttons in the
   title bar.
2. Cmd-Q from the menu bar quits the app cleanly. Closing the
   window via the red close button does the same. State teardown
   (close_file under lock, server.shutdown) runs in both cases.
3. Clicking **Open** in the topbar shows a native file chooser. With
   pywebview's bridge in place, the chooser supports the `.aaf` file
   filter (cleaner than the osascript fallback).
4. Inside the bundled app: walking the Phase-3 reference clip
   returns `PW_310_ISO1_B PTN=1`. End-to-end proof that pyaaf2 +
   pywebview + Flask all coexist and the chain-walk machinery
   survives the bigger bundle.
5. `aafbrowser web` from a pip install is unchanged — opens the user's
   default browser, keeps the existing /api/pick_file flow.
6. Bundle launches under hardened runtime + entitlements. Real
   Developer ID notarization (when secrets are configured in CI)
   produces a signed + stapled .dmg without a Gatekeeper prompt.
7. `pytest` exits 0. Webview integration is not unit-tested (would
   require a display); the data-layer tests still cover the Flask
   endpoints and the chain-walk core.
8. Final .dmg size is **under 60 MB** (PyObjC adds ~30-40 MB).

## Risks to flag rather than paper over

- **PyObjC + Python 3.14 compatibility.** PyObjC 12.x (the version
  pywebview 6.2.1 will pull) is recent; should support 3.14. If it
  doesn't, smoke-test step 1 reveals it and we either pin Python to
  3.13 in the bundle or pin pywebview to a version compatible with
  3.14.
- **PyInstaller bundling pywebview's WebKit framework.** Phase 4's
  bundle didn't need WebKit. Adding it is the riskiest packaging
  step. Mitigation: smoke-test build in step 3 immediately after
  the spec change.
- **Hardened runtime + WKWebView entitlements.** WKWebView may need
  additional entitlements (e.g. JIT for JavaScriptCore, network
  client for fetching local-but-still-HTTP). Verify in step 3 by
  signing with `--options runtime` and launching. Add to
  `entitlements.plist` whatever proves needed.
- **CFBundleDocumentTypes still works.** Phase 4's `.aaf` association
  is a cold-start argv path. With pywebview now in the loop, confirm
  the path arg still threads through to `state.open_file` before
  pywebview takes over the main thread.
- **Bundle size growth.** ~30-40 MB more than Phase 4's 12 MB .dmg.
  Acceptable; flag in the README.
- **`webview.start()` is single-window-on-main-thread.** pywebview
  has well-known constraints — Flask server *must* run in the
  background thread (we already do this), and any pywebview API
  calls *must* be made from the main thread or via `webview.windows[0].evaluate_js`.
  The Bridge class for the file picker is the only Python-from-JS
  call; it runs in the main thread automatically.

## Completion report

When Phase 5 lands, produce
`docs/completion_reports/phase5-completion-report.md` covering:

- Acceptance-criteria checklist.
- Any deviations with rationale.
- Bundle size delta (Phase 4: 12 MB .dmg → Phase 5: ?).
- Cold-start time delta (Phase 4: 0.36 s → Phase 5: ?).
- Phase-3 cross-check inside the new bundle.
- GUI ergonomics — what the native shell feels like vs the browser
  tab. Specifically whether the lack of a custom menu bar feels
  rough or fine.
- Phase 6 candidate ideas (no commitment).
