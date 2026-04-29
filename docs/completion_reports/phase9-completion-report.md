# Phase 9 — Completion Report

**Status:** Shipped (CLI parity in code; notarization wired but
requires the user's Apple credentials to actually exercise).
**Brief:** [`docs/phase9-brief.md`](../phase9-brief.md)

## What landed

1. **Four new CLI subcommands** in `aafbrowser/cli/__main__.py`:
   - `aafbrowser session <file.aaf> [--json]`
   - `aafbrowser tracks <file.aaf> [--json]`
   - `aafbrowser clips <file.aaf> --slot N [--json]`
   - `aafbrowser sources <file.aaf> [--json] [--unused]`
   Each consumes `aafbrowser.core.operator` directly (no Flask)
   and follows the existing CLI's file + sha256 header convention.
   `--json` envelope mirrors the corresponding `/api/...` endpoint.
2. **Pan-channel detection bugfix** in `core/operator.py`. The
   Phase 8 `_track_pan_channel` was matching on
   `"pan" in op_name.lower()`, which caught Avid's "Audio Pan"
   operation and treated its int-valued ConstantValue parameters
   as ratios → every Avid track was getting `pan_channel="R"`.
   Tightened to require operation name `== "Mono Audio Pan"` (the
   Premiere-specific operation per the recovery doc) and the
   parameter to be a `ConstantValue` wrapping an `AAFRational`.
3. **Read-only invariant** extended to cover all four new
   commands in `tests/test_readonly.py` (file SHA-256 unchanged
   after each command).
4. **Notarization workflow** in `packaging/macos/build.sh`,
   gated on `NOTARIZE=1`:
   - Two credential paths: `NOTARYTOOL_PROFILE` (recommended for
     local; one-time `notarytool store-credentials` setup) or
     `APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD` env vars
     (for CI).
   - Submits the signed `.dmg` via `xcrun notarytool submit
     --wait`, staples on success, validates.
   - Without `NOTARIZE=1` the section is inert; default local
     iter unchanged.
5. **`docs/notarization-setup.md`** covering one-time credential
   storage, local notarized build, CI build env vars, fast-iter
   path (skip notarization), and troubleshooting common errors.

Total: 255 tests pass. 11 new CLI tests across `test_cli.py`.

## Acceptance criteria — checklist

| # | Criterion | Status |
|---|---|---|
| 1 | `pytest` exits 0 with new + existing tests passing | ✅ — 255 tests |
| 2 | Each new CLI command produces sensible output on PWD_310 | ✅ — manual smoke + per-command tests |
| 3 | `--json` output parses with `json.loads` and matches the corresponding `/api/...` shape | ✅ — verified in `test_session_json` / `test_tracks_json` / `test_clips_json` / `test_sources_json` |
| 4 | Read-only invariant: input file SHA-256 unchanged | ✅ — `test_every_command_leaves_input_byte_identical` extended |
| 5 | `bash packaging/macos/build.sh` without `NOTARIZE=1` produces a working .dmg as before | ✅ — verified after the build script change |
| 6 | With `NOTARIZE=1` + valid credentials: stapled .dmg passes `xcrun stapler validate` | ⚠️ — code path wired and gated; not exercised this session (user has the Apple credentials) |

## CLI output samples (PWD_310)

**`aafbrowser session samples/PWD_310_LC_10-07-2025.aaf`:**
```
file: /Users/amohr/programming/AAF_Browser/samples/PWD_310_LC_10-07-2025.aaf
sha256: fecb030b3948f37c8d6bed6605b148e8a9fb1a1708aace316edb36e1a65d666d

composition: PWD_310_LC_10-07-2025.Exported.01
  mob counts: comp=48 master=1568 source=3032
tracks: 25 audio · 0 video · 2 timecode
clips: 5981 total
timecode: rate=30000/1001 fps=30 drop=True start=00:59:50;00 (frame 107592)
duration: 00:42:09;02 (2529.060s)
authored by: Avid Media Composer 24.12.1 · AAFSDK (Win64) · kind=avid
modified: 2025-10-07T17:12:33
audio sources: 2014 · rates={'48000/1': 2014} · bits={24: 2014} · channels={1: 2014}
```

**`aafbrowser tracks samples/PWD_310_LC_10-07-2025.aaf` (head):**
```
  A1    slot=3    kind=audio clips=517   HOST
  A2    slot=4    kind=audio clips=131   ANNOUNCER
  A3    slot=5    kind=audio clips=400   JIMMY
  A4    slot=6    kind=audio clips=370   CELEB GUEST
  A5    slot=7    kind=audio clips=233   CONTESTANT 1
```

**`aafbrowser clips ... --slot 3` (head):**
```
slot 3: 517 clips

  [   0] ts=0 len=871 <Filler>
  [   1] ts=871 len=8 <Transition>
  [   2] ts=879 len=127 [PW_310_ISO1_B] PW_310_ISO1_B.01.new.02 recovery=recoverable
  [   3] ts=1006 len=98 [PW_310_ISO1_B] PW_310_ISO1_B.01.new.02 recovery=recoverable
```

**`aafbrowser sources ...` (head):**
```
231 source mobs (used)

  used= 490  PW_310_ISO1_B                             TapeDescriptor
  used= 352  PW_310_ISO1_A                             TapeDescriptor
  used= 256  PW_310_ISO6_A                             TapeDescriptor
  used= 150  PW_310_ISO6_B                             TapeDescriptor
```

## Read-only invariant verification

`tests/test_readonly.py::test_every_command_leaves_input_byte_identical`
runs every CLI command (now including the four new ones, in both
human and `--json` form) against a per-test copy of the fixture and
asserts the SHA-256 is unchanged after each. Passes.

## Notarization status

The build-script extension is wired and inert by default. Locally
testing notarization end-to-end requires:

1. A Developer ID Application certificate in your keychain.
2. An app-specific password generated at appleid.apple.com.
3. Your Apple Team ID.
4. One-time `xcrun notarytool store-credentials AC_PASSWORD ...`
5. Then: `NOTARIZE=1 NOTARYTOOL_PROFILE=AC_PASSWORD CODESIGN_IDENTITY="Developer ID Application: ..." bash packaging/macos/build.sh`

Documented step-by-step in `docs/notarization-setup.md`.

## Bundle size

Phase 8: 16 MB .dmg. Phase 9: 16 MB .dmg — pure Python + script
changes, no new dependencies, no new static assets.

## Phase 10+ candidate ideas

The roadmap from the Phase 9 brief:

- **Sparkle auto-update** for the .app — would close the loop on
  shipping updates without users manually fetching .dmgs.
- **GitHub Actions release workflow** — wire the existing
  `.github/workflows/macos-release.yml` (from Phase 4) to the
  notarization secrets; cut tagged releases unattended.
- **AIFCDescriptor coverage** — the audio descriptor parser
  handles WAVE + PCM but AIFC is untested. Pro Tools-authored AAFs
  may surface this.
- **`walk` CLI to use `walk_chain_tree`** — existing `walk`
  command uses the flat Phase 3 walker; the Phase 7 tree-walk
  adds combiner sub-walk. Mechanical change.
- **Operator workflows beyond pull-list** — conform triage / AAF
  diff (excluded per user direction this round, but the operator
  module is now stable enough to host them).
