# AAF Browser — Phase 4 Brief

## Goal

Make AAF Browser usable by people who don't live in a terminal. Two
specific deliverables:

1. **Native macOS file picker** in the existing web GUI. The current
   "Open" dialog asks the user to *paste an absolute path* into a text
   field; replace it with a real `osascript`-driven file chooser that
   works for both the existing pip distribution and the new bundled
   app.
2. **Distributable macOS app.** A `.dmg` containing a self-contained
   `.app` bundle, built by PyInstaller, signed with an Apple Developer
   ID, notarized, and published to GitHub Releases via a tag-triggered
   Actions workflow. Existing pip distribution stays unchanged.

Project context: read `docs/PROJECT_OVERVIEW.md`, repo-root
`CLAUDE.md`, and the Phase 1-3 completion reports under
`docs/completion_reports/`. This brief covers Phase 4 only.

User decisions (locked in):

- **Architecture: arm64-only.** Apple Silicon Macs only.
- **Signing: Developer ID + notarization** in CI. Local builds without
  the cert fall back to ad-hoc signing.

## Scope

In:

- `POST /api/pick_file` endpoint (macOS only via `osascript`); returns
  `501 Not Implemented` on non-darwin so the existing text-field
  dialog remains a clean fallback.
- Frontend rewire: `Open` button calls `/api/pick_file` first; on
  success skips the dialog and submits the chosen path directly to
  `/api/open`. On 501 it falls back to today's text input.
- `aafbrowser/_app_entry.py` — bundle entrypoint module that picks a
  free port, starts wsgiref in a daemon thread, opens the system
  browser, blocks on the server, and handles SIGTERM / SIGINT.
- `POST /api/quit` with same-origin guard, deferred `os._exit(0)`
  after `state.close_file()`. Topbar `Quit` link plus a `beforeunload`
  → `navigator.sendBeacon('/api/quit')` so closing the browser tab
  also stops the server (the actual common case for a single-user
  local app).
- `packaging/macos/aafbrowser.spec` — checked-in PyInstaller spec.
  Targets arm64. Uses `collect_submodules('aaf2')` to defend against
  pyaaf2's dynamic class registration.
- `packaging/macos/build.sh` — local build / sign / dmg helper. Uses
  `CODESIGN_IDENTITY` env var if set, ad-hoc otherwise.
- `packaging/macos/Info.plist` overrides + `entitlements.plist`
  (minimal: `com.apple.security.network.server` and any JIT entitlements
  PyInstaller's bootloader requires).
- `packaging/macos/icon.png` (placeholder 1024×1024) + a build step
  that produces `icon.icns` via `iconutil`.
- `.aaf` file association via `CFBundleDocumentTypes` and a custom
  UTI declaration (first-launch argv-based; document the
  "open with" caveat for the already-running case).
- `.github/workflows/macos-release.yml` — tag-triggered, signed,
  notarized, stapled, .dmg uploaded to release.
- `[project.optional-dependencies] mac-build = ["pyinstaller>=6"]`.
- README and `PROJECT_OVERVIEW.md` updates.

Out:

- Auto-update / Sparkle (defer — requires more infra).
- Universal2 (Intel) builds.
- Native AppKit UI / `rumps` menubar wrapper. The web GUI is the UI.
- In-process `NSApplicationOpenFile` handler (would require an AppKit
  event loop). Open-with works only on cold start in v1.
- App Store distribution.

## Non-negotiable invariants (from CLAUDE.md, restated)

- **Read-only.** The bundled app uses the same pyaaf2 read-only flow
  as the CLI. No write paths are introduced.
- **Lock-protected pyaaf2 access.** All new endpoints
  (`/api/pick_file`, `/api/quit`) acquire `state_mod.state_lock()`
  when touching pyaaf2 state.
- **Pure core.** The new `_app_entry.py` lives at the package root,
  not in `core/`. Static assets remain co-located with
  `aafbrowser.web`.

## Verified decisions (do not re-litigate during build)

- **PyInstaller, not py2app or Briefcase.** PyInstaller is the
  current standard for pure-Python tools targeting macOS .app
  distribution.
- **`--onedir` + `--windowed` mode.** Required to produce a true
  `.app` bundle (not a single binary).
- **`osascript -e 'choose file' -e 'POSIX path of result'`** for the
  picker. No PyObjC dependency. User cancel (exit 1 with `-128`) is
  treated as a clean `{"path": null}` response.
- **`collect_submodules('aaf2')`** in the spec to defend against
  pyaaf2's dynamic class registration. Smoke-test a real AAF after
  the first bundle build before declaring step 4 done.
- **Ad-hoc signing path stays in `build.sh`** for local dev even
  after Developer ID is in place for CI.

## Build sequence

One commit per step. Order chosen so each step is independently
testable.

1. **`/api/pick_file` + frontend rewire.** New endpoint in `app.py`,
   handles `osascript` cancel gracefully, returns 501 on non-darwin.
   Frontend calls it first, falls back to text dialog on 501. Tests
   via Flask test client with `subprocess.run` mocked.
2. **`/api/quit` + Quit link + beforeunload `sendBeacon`.**
   Same-origin guard, deferred exit via `threading.Timer`,
   `state.close_file()` under lock first. Topbar link, frontend
   beforeunload handler. Tests via test client with `os._exit`
   mocked.
3. **`aafbrowser/_app_entry.py`.** Port-pick via
   `socket.bind(('127.0.0.1', 0))`, daemon thread for wsgiref,
   webbrowser.open, signal handlers. Optional argv-based AAF
   pre-open. Usable as `python -m aafbrowser._app_entry` for
   testing without a bundle.
4. **PyInstaller spec.** `packaging/macos/aafbrowser.spec` with
   entry script, `datas` for static folder, hidden imports for
   pyaaf2, BUNDLE block with bundle_identifier, info_plist
   overrides (CFBundleVersion, CFBundleDocumentTypes for .aaf,
   UTExportedTypeDeclarations). Smoke-test with
   `samples/Password_Mix_Audio_Tracks.aaf` before commit.
5. **Icon.** Placeholder PNG + iconset build script + `.icns`
   referenced from spec. Design is "AB" letterform; can swap later.
6. **Local build script.** `packaging/macos/build.sh`: pyinstaller,
   codesign (Developer ID if env var set, ad-hoc otherwise),
   create-dmg, output to `dist/AAF-Browser-${version}-arm64.dmg`.
7. **Entitlements.** `packaging/macos/entitlements.plist` with the
   minimal set needed for hardened runtime + local network server.
   Use with `codesign --options runtime --entitlements ...`.
8. **GitHub Actions workflow.**
   `.github/workflows/macos-release.yml` triggered on `v*` tag.
   Imports Developer ID cert into temp keychain, runs build.sh,
   notarizes via `xcrun notarytool`, staples, uploads dmg to
   release. CI just sets env vars and calls `build.sh`.
9. **README + PROJECT_OVERVIEW updates.** Move pip install to a
   "Developers / CLI" subsection. Add a top-of-page "macOS app"
   section. Document the open-with caveat and the Sequoia
   "Open Anyway" fallback. Update phasing table.
10. **Phase 4 completion report.** Acceptance-criteria checklist,
    deviations, validation against the Phase-3 PWD_310 numbers
    inside the bundled app, perf notes (cold-start time of the
    .app), risks for Phase 5.

## Acceptance criteria

1. `aafbrowser web samples/Password_Mix_Audio_Tracks.aaf` from a pip
   install: clicking the topbar `Open` button shows a native macOS
   file chooser; selecting an AAF loads it. Cancelling closes the
   chooser without error.
2. `python -m aafbrowser._app_entry samples/Password_Mix_Audio_Tracks.aaf`
   launches the GUI in the system browser, the file is preloaded,
   and Cmd-C in the launching terminal cleanly stops the server.
3. `bash packaging/macos/build.sh` produces a working
   `dist/AAF-Browser-${version}-arm64.dmg` on a developer machine.
   Mounting the dmg, dragging the .app to /Applications, and
   double-clicking launches the GUI in the browser.
4. Inside the bundled app: opening
   `samples/PWD_310_LC_10-07-2025.aaf` and walking the chain on the
   HOST track at sample 29056227 returns `PW_310_ISO1_B PTN=1`,
   matching the Phase 3 reference. End-to-end proof that the bundle
   preserves pyaaf2's dynamic-class behavior.
5. Topbar `Quit` link stops the server cleanly. Closing the browser
   tab also stops the server.
6. Pushing a `vX.Y.Z` tag triggers
   `.github/workflows/macos-release.yml`; the dmg appears on the
   release page within a reasonable time (target: under 15 minutes).
7. Downloading the released dmg on a clean Mac, installing, and
   launching produces no Gatekeeper prompt (signed + notarized +
   stapled).
8. `pytest` exits 0 with new tests added. Target: 165 + ~6 new
   tests passing.
9. SHA-256 of every sample is unchanged after a full GUI session in
   the bundled app (read-only invariant preserved).

## Risks to flag rather than paper over

- **pyaaf2 dynamic imports breaking inside the bundle.** Highest-
  probability failure mode. Mitigation in step 4: smoke-test the
  bundle with a real AAF immediately and add `hiddenimports` if
  necessary.
- **wsgiref single-threaded.** A long `/api/find` blocks `/api/quit`
  too. Acceptable for v1; document as a known limitation. Phase 5
  would upgrade the server.
- **Cmd-Q from Dock kills via SIGKILL after a timeout** (no AppKit
  event loop). pyaaf2 is read-only so no corruption risk.
- **Open-with `.aaf` from Finder only fires the argv path on cold
  start.** Documented limitation.
- **CI cert leakage.** Standard temp-keychain pattern; cert is
  imported then deleted at workflow end.
- **macOS Sequoia "right-click → Open" no longer reliable for
  unsigned apps.** Documented fallback: System Settings → Privacy &
  Security → Open Anyway. Should not affect us once Developer ID
  signing + notarization are in place.

## Completion report

When Phase 4 lands, produce
`docs/completion_reports/phase4-completion-report.md` covering:

- Acceptance-criteria checklist.
- Any deviations with rationale.
- Validation against the Phase-3 PWD_310 numbers inside the bundled
  app.
- Cold-start performance of the bundled app (open-to-browser-tab
  time).
- Final .dmg size.
- GUI ergonomics — what the file picker and Quit affordance feel
  like in real use.
- Recommendations for a possible Phase 5 (no commitment).
