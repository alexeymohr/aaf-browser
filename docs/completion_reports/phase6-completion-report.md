# Phase 6 — Completion Report

**Status:** Shipped.
**Brief:** [`docs/phase6-brief.md`](../phase6-brief.md)

## What landed

A new operator-first surface for the web/macOS app:

- New module **`aafbrowser/core/operator.py`** with `Track`, `Clip`,
  `list_tracks(handle)`, `list_clips(handle, slot_id)`, and
  `pick_topmost_composition(handle)`. Composes `core/chain.walk_chain`
  to recover recorder mic identity per clip. Includes
  Avid-OperationGroup-wrapper unwrap at both the slot-segment level
  and the per-clip level — without this, real Avid AAFs surface as
  one opaque blob per track.
- New web endpoints **`GET /api/tracks`** and
  **`GET /api/track/clips?slot=<int>`** in `web/app.py`, both following
  the existing `state.state_lock()` + `_require_open` pattern.
- New frontend layout: top-level **view tabs** (Tracks / All Mobs /
  CFB) replacing the previous left-pane AAF/CFB sub-tabs. Body class
  drives a CSS-grid layout swap so the shared inspector slides into
  whichever column the active view assigns it (column 3 under
  view-tracks, column 2 otherwise).
- New Tracks pipeline in `static/app.js` (~250 lines): track-list
  renderer, clip-tree renderer with disclosure expansion to the
  source MasterMob's full object graph, operator-summary inspector
  header for clips.
- New unit tests (`tests/test_operator.py`, 13 tests) + new web-API
  tests (8 in `tests/test_web_api.py`) + extended read-only
  invariant test covering the new endpoints.
- New programmatic fixture `multi_track_aaf` in `tests/conftest.py`
  (CompositionMob with 2 audio + 1 video slot, multi-component
  Sequence on slot 1) — used by the operator + API tests.
- Brief, README, and PROJECT_OVERVIEW updated.

Total: 203 tests pass. Zero regressions in pre-Phase-6 tests (181
prior + 22 new).

## Acceptance criteria — checklist

| # | Criterion | Status |
|---|---|---|
| 1 | Tracks tab is default landing on `aafbrowser web <file>` | ✅ |
| 2 | Track click populates clips with operator-meaningful labels | ✅ |
| 3 | Disclosure triangle on each clip recursively expands child structure | ✅ — expands into the source MasterMob's full object tree |
| 4 | Right inspector shows operator-summary + raw object dump | ✅ |
| 5 | Header tab strip switches to All Mobs / CFB cleanly; existing UIs unchanged behind those tabs | ✅ |
| 6 | Mic identities on Password sample match corpus-validation results | ✅ — see "Manual cross-check" below |
| 7 | Read-only invariant: SHA-256 unchanged after exercising new endpoints | ✅ — extended `tests/test_web_readonly.py` |
| 8 | `pytest` exits 0; new + existing tests pass | ✅ — 203 passed |
| 9 | macOS bundle still builds; bundle size delta ≤ 1 MB | ✅ — rebuilt; .dmg is 14 MB (same as Phase 5); end-to-end verified via the bundled binary against PWD_310 |

(9) was rebuilt and verified this session: `bash packaging/macos/build.sh`
produced `dist/AAF Browser.app` and `dist/AAF-Browser-v0.1.0-arm64.dmg`
(14 MB, same as Phase 5). Launching the bundle binary against
`samples/PWD_310_LC_10-07-2025.aaf` returned the expected Tracks
layout (25 tracks, named HOST/ANNOUNCER/JIMMY/etc.) and recovered
the same 285 recorder-source clips on the HOST track with identical
mic-identity distribution as the in-process verification — the
operator-first surface ships in the .app exactly as in
`aafbrowser web` from a pip install.

## Deviations from the brief

1. **Track-level OperationGroup unwrap was added.** The brief said
   "treat any non-Sequence segment as a single clip." That was correct
   for the synthetic chain_aaf fixture but produced unusable output on
   the canonical PWD_310 sample, where every track's segment is an
   OperationGroup wrapping a Sequence (audio level/EQ automation). The
   operator module now peels this wrapper via
   `_peel_track_wrapper(segment)` so the inner Sequence's components
   become the visible clip list. Documented in the operator module
   docstring.

2. **Per-clip OperationGroup unwrap was added.** Same Avid pattern at
   the per-clip level: each clip is itself wrapped in an Audio Gain
   OperationGroup. The operator module unwraps single-input
   OperationGroups to the inner SourceClip via
   `_unwrap_operation_group(og)` so the chain walk recovers the
   recorder identity. Without this, every clip on PWD_310's HOST track
   was unattributed.

3. **Recorder-source filter relaxed.** The brief and
   `identifying-clip-channels.md` both said the Gotcha-3 filter is
   `terminal_reason == "essence" AND mob_class == "SourceMob" AND PTN
   > 0`. In practice on PWD_310, real Avid SourceMobs frequently
   terminate their slot's segment as a zero-mob SourceClip
   (`terminal_reason="no_source_id"`) rather than an empty Sequence
   (`terminal_reason="essence"`). Both shapes mean "we hit the recorder
   mob with no further chain." The operator module now requires only
   `mob_class == "SourceMob" AND PTN > 0`. Recorder-source detection
   on PWD_310 went from 0 clips to 285 on the HOST track after this
   change. Logged in the operator module's `_build_source_clip_clip`
   comment.

4. **Mic-identity walk-back to last named SourceMob.** When the
   immediate-terminal SourceMob is unnamed (a "physical source mob"
   in pyaaf2 terminology), the operator falls back to the most recent
   named SourceMob in the hop list. This surfaces names like
   `PW_310_ISO1_B` instead of empty strings.

5. **CompositionMob heuristic edge case logged via response.** The
   `/api/tracks` response includes a `topmost_composition: {mob_id,
   name}` field so the user can see which CompositionMob the heuristic
   picked. On a 48-CompositionMob file (PWD_310) the heuristic
   correctly chose `PWD_310_LC_10-07-2025.Exported.01` (31 slots),
   the obvious timeline composition.

## Manual cross-check on real samples

Tested against `samples/PWD_310_LC_10-07-2025.aaf`:

- 48 CompositionMobs total → heuristic picked
  `PWD_310_LC_10-07-2025.Exported.01` (31 slots).
- 25 audio + video tracks surfaced; first 8 audio tracks have
  operator-meaningful names: HOST, ANNOUNCER, JIMMY, CELEB GUEST,
  CONTESTANT 1, CONTESTANT 2, STUDIO AUD L, STUDIO AUD R.
- Per-track clip breakdowns (recorder-source clips only):

| Track | Components | Recorder clips | Top mic identities |
|---|---|---|---|
| HOST | 517 | 285 | PW_310_ISO1_B (157), PW_310_ISO1_A (126), PW_310_ISO9_B (1), Password_041322_EP_103B (1, inserted from another episode) |
| ANNOUNCER | 131 | 49 | PW_310_ISO3_B (21), PW_310_ISO5_B (12), PW_310_ISO5_A (9), PW_310_ISO1_B (4) |
| JIMMY | 400 | 193 | PW_310_ISO1_B (109), PW_310_ISO1_A (83), PW_315_ISO1_B (1) |
| CELEB GUEST | 370 | 187 | PW_310_ISO1_B (104), PW_310_ISO1_A (83) |
| CONTESTANT 1 | 233 | 125 | PW_310_ISO1_B (116), PW_310_ISO3_A (4), PW_310_ISO1_A (3) |
| CONTESTANT 2 | 277 | 137 | PW_310_ISO1_B (81), PW_310_ISO1_A (53) |
| STUDIO AUD L | 424 | 264 | PW_310_ISO6_B (143), PW_310_ISO1_A (98), PW_310_ISO6_A (11) |
| STUDIO AUD R | 409 | 292 | PW_310_ISO6_B (143), PW_310_ISO6_A (135) |

The mic-identity distribution matches the pattern documented in
`docs/identifying-clip-channels.md`: per-track recovery, with
small-N inserts visible (cross-episode reuse, sound effects). The
non-recorder clips on each track are Filler (silence between clips),
Transition (crossfades), and a small handful of multi-input
OperationGroups (~30% non-convergence rate predicted by the corpus
validation, holds here too).

The sub-100% recorder-source rate per track is expected — Filler and
Transition components are real timeline elements, not method failures.

## What works, what's deferred

Works:

- Operator-meaningful track list with names, kinds, ordinals.
- Per-clip recovered mic identity for the bulk of an Avid session
  (everything that isn't a multi-input OperationGroup or
  CompositionMob terminal).
- Disclosure-triangle expansion on clips, drilling into the source
  MasterMob's full AAF object tree via the existing `/api/object`
  endpoint.
- Operator-summary header on the inspector for selected clips.
- Read-only invariant preserved end-to-end; full test suite green.

Deferred to a Phase 7 (no commitment yet):

- **Multi-input OperationGroup sub-walk.** When a clip's chain hits
  an OperationGroup that combines multiple SourceClips (the ~30%
  non-convergence case from the corpus validation), Phase 6 marks
  the clip as `terminal_reason="operation_group"` rather than
  recursing into each input. Recovering the per-input recorder
  identity would lift convergence to ~95%+.
- **Operator-summary fields beyond chain-walk derived.** Head/tail
  handles, sample rate, channel layout, source-file path
  (Locator URL), online/offline detection.
- **CLI parity.** `aafbrowser tracks`, `aafbrowser clips --slot N`.
  The core/operator module is structured to make these mechanical to
  add.
- **Source pull list across all tracks.** "Give me everything to
  ask the recordist for" — a deduplicated list of every external
  essence file referenced by the file.
- **Conform triage / sanity report.** Offline media, missing source
  files, sample-rate mismatches, suspicious handles.
- **AAF diff.** Compare two AAFs; surface what changed.
- **Premiere-specific channel recovery.** Stereo-split via Mono
  Audio Pan, polywav-import limitation surfacing — driven by
  `docs/premiere-aaf-channel-recovery.md`.
- **Find / Walk as top-level tabs.** Phase 6 kept Find as the bottom
  drawer and Walk as the inspector-header affordance. Promoting
  them to top-level tabs is a natural Phase 7 polish.

## What feels different

Loading PWD_310 and clicking the **HOST** track now produces a
visible roster of 285 clips, each tagged with the recorder mic that
fed it (`PW_310_ISO1_B` 157 times, `PW_310_ISO1_A` 126 times, plus
two anomalies — one cross-episode insert, one boom mic). That's a
concrete, operator-readable answer to "what mics am I dealing with on
this track?" — visible immediately on the default landing, with no
manual chain-walking from a Mob list.

The previous default landing (a flat list of 4,648 Mobs grouped by
class) shifted the cognitive load to the user: pick a Mob, drill into
its slots, walk the chain by hand. The Tracks view collapses that
into one click. The geek surface is still one tab away when you need
it.

## Bundle size delta

Phase 5: 14 MB .dmg. Phase 6: 14 MB .dmg (no measurable delta at
1 MB precision). Frontend grew ~280 lines of JS + ~150 lines of CSS;
no new Python dependencies; PyInstaller spec unchanged. End-to-end
verification against PWD_310 inside the bundle matched the in-process
results exactly (same 285 recorder-source clips on HOST track, same
mic-identity distribution).

## Phase 7 candidate ideas

- Multi-input OperationGroup sub-walk (the ~30% convergence lift the
  corpus validation flagged).
- Source pull list (cross-track dedup of essence file references).
- Operator-summary fields v2: handles, sample rate, channel layout,
  source file Locator URL, online/offline detection.
- CLI parity for the operator module.
- Premiere-specific channel recovery, branching on stereo-split via
  Mono Audio Pan vs polywav-import-with-no-recovery.
- AAF diff (two-file orchestration).
- Conform triage report (single-screen "is this file safe to
  conform?").
