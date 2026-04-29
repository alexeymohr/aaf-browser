# Phase 8 — Completion Report

**Status:** Shipped.
**Brief:** [`docs/phase8-brief.md`](../phase8-brief.md)

## What landed

1. **Authoring-driven format dispatch** in `core/operator.py`:
   - `detect_authoring_kind(handle)` returns
     `"avid"` / `"premiere"` / `"protools"` / `"unknown"` based on
     `Header.IdentificationList[-1].ProductName` (case-insensitive
     substring match).
   - `AuthoringInfo` dataclass gains `kind` field.
2. **Premiere stereo-split recovery via Mono Audio Pan**:
   - `_track_pan_channel(slot_segment)` reads a slot-level
     `Mono Audio Pan` OperationGroup parameter; returns `"L"` if
     pan ≈ 0.0, `"R"` if ≈ 1.0, `None` otherwise.
   - `Track` dataclass gains `pan_channel`.
   - `list_tracks` populates it.
3. **Per-clip recovery classification** in `Clip`:
   - New fields `recovery_status` (`"recoverable"` /
     `"ambiguous"` / `"unrecoverable"`) and `recovery_method`.
   - `_classify_recovery(...)` dispatches on `authoring_kind`,
     `pan_channel`, chain-walk results, and source-mob name
     pattern. Premiere paths come first when the file is Premiere;
     Avid path catches the generic recorder-source case.
   - Premiere stereo-split clips get a display suffix (`(L)` /
     `(R)`) on `mic_identity` when the source name doesn't already
     include `_L`/`_R`.
   - Multi-input combiner top-level clip's `recovery_status`
     aggregates from its `sub_clips` (all-recovered ⇒ recoverable;
     all-unrecoverable ⇒ unrecoverable; mixed ⇒ ambiguous).
4. **Frontend rendering**:
   - Clip-row visual variant: `unrecoverable` → faint red
     border-left + muted text; `ambiguous` → warm-yellow border.
   - Track-row: `pan_channel` displays as a small `L`/`R` pill
     next to the track name (Premiere stereo-split signal).
   - Inspector summary card: new `Recovery` and `Recovery method`
     rows.
5. **Synthetic Premiere fixtures** in `tests/conftest.py`:
   - `premiere_stereo_split_aaf`: CompositionMob with two slots,
     each `slot.segment` is a `Mono Audio Pan` OperationGroup
     with a ConstantValue parameter (0.0 = LEFT / 1.0 = RIGHT)
     wrapping a SourceClip → MasterMob `Audio 1_L` / `Audio 1_R`.
   - `premiere_polywav_aaf`: identical-metadata MasterMobs named
     `Audio 1` / `Audio 2`, no Pan, no `_L`/`_R`, no PTN.
   - Both fixtures override the default `PyAAF` Identification
     entry with an Adobe Premiere Pro one via `_add_premiere_identification(f)`.

Total: 244 tests pass. 9 new tests across `test_operator.py`.

## Acceptance criteria — checklist

| # | Criterion | Status |
|---|---|---|
| 1 | `pytest` exits 0 with new + existing tests passing | ✅ — 244 tests |
| 2 | Premiere stereo-split fixture: `pan_channel="L"`/`"R"` per track; clips `recoverable` with method `premiere_stereo_split_pan` | ✅ — verified in `test_premiere_stereo_split_clip_recovery` |
| 3 | Premiere polywav fixture: clips `unrecoverable` with method `premiere_polywav_indeterminate` | ✅ — verified in `test_premiere_polywav_clip_unrecoverable` |
| 4 | PWD_310 (Avid) recovery numbers from Phase 7 unchanged | ✅ — HOST first recorder clip still mic=PW_310_ISO1_B status=recoverable method=avid_chain_walk; full track recovery rate maintained |
| 5 | Bundled `.app` rebuilds + launches; UI surfaces L/R suffixes + recovery_status variants | ✅ — bundle rebuilt at `dist/`, end-to-end flow visible in WKWebView |
| 6 | Read-only invariant preserved | ✅ — no new endpoints; all field additions are read-only on existing endpoints |

## Authoring detection results

- `samples/PWD_310_LC_10-07-2025.aaf` → **`avid`** (ProductName:
  "Avid Media Composer 24.12.1") — confirmed by
  `test_detect_authoring_kind_pwd_310_is_avid`.
- Synthetic Premiere fixtures → **`premiere`** (ProductName:
  "Adobe Premiere Pro 24.0").

## Phase 7 regression check

PWD_310 HOST track first recorder clip:
- `mic_identity = "PW_310_ISO1_B"` (unchanged)
- `is_recorder_source = True` (unchanged)
- `recovery_status = "recoverable"` (new — Phase 8)
- `recovery_method = "avid_chain_walk"` (new)

All Phase 7 fields (`audio_sample_rate`, handles, `source_locators`,
`sub_clips`, etc.) unchanged on Avid AAFs.

## Real-Premiere validation note

`samples/` only contains Avid AAFs (PWD_310 + two other Password
shows). The Premiere AAFs from the corpus validation
(`CasaLuxe EP 201/202/203`, `SavingJones`) aren't in the local
samples dir. Validation against real Premiere AAFs is opt-in for
when the user makes one available — the test fixtures mirror the
documented patterns from `docs/premiere-aaf-channel-recovery.md`,
but real-world variations weren't exercised this phase.

## Deviations from the brief

1. **Pan parameter tolerance widened.** The brief said "within 0.1
   of the endpoints"; implementation uses `< 0.1` for L and `> 0.9`
   for R. Equivalent semantically for hard-L/hard-R; documented in
   the function docstring.
2. **Polywav heuristic uses exact `Audio N` pattern.** The brief
   suggested "name doesn't end in `_L`/`_R` and doesn't have other
   discriminators". Implementation tightens to
   `^Audio \d+$` regex match — strict enough to avoid false
   positives on Avid sessions that have differently-named sources.
   If real Premiere AAFs use other patterns (e.g.
   `Audio Channel N` or localized strings), this can be widened in
   a Phase 8.x sub-step.
3. **mic_identity gets a display suffix on Premiere stereo
   splits.** Per the brief, only when the source mob name doesn't
   already end in `_L`/`_R`. Implementation matches; documented in
   the test (`Audio 1_L` displays as `Audio 1_L`, not
   `Audio 1_L (L)`).

## Bundle size

Phase 7: 16 MB .dmg. Phase 8: same — pure code changes, no new
dependencies, no new static assets.

## Phase 9 readiness

Ready to start. The CLI parity work (`aafbrowser tracks`,
`aafbrowser clips`, `aafbrowser session`, `aafbrowser sources`)
mirrors the operator API surface that's now stable through Phase 8.
Notarization is a separate dist-hygiene track that can be done in
parallel with the CLI work.
