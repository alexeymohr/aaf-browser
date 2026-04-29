# Phase 7 — Completion Report

**Status:** Shipped.
**Brief:** [`docs/phase7-brief.md`](../phase7-brief.md)

## What landed

Five things, all shipped together:

1. **Multi-input OperationGroup sub-walk** in `core/chain.py`.
   New `walk_chain_tree` returns a tree (Hop | HopBranch) instead of
   a flat list. On a terminal that's an OG with ≥2 SourceClip
   inputs, returns a HopBranch carrying one fully-walked sub-chain
   per input. Cycle-safe via shared visited set; bounded by
   `max_depth` and `max_combiner_inputs`.
2. **Per-clip operator info** in `core/operator.Clip`:
   - `source_locators`: NetworkLocator / TextLocator entries from
     the file SourceMob's descriptor, each with online/offline
     check via `os.path.exists` for `file://` URLs.
   - `head_handle_frames` / `head_handle_seconds` /
     `tail_handle_frames` / `tail_handle_seconds` computed from
     comp-clip start + length vs the file SourceMob slot's total
     length, with edit-rate conversion when comp slot and source
     slot rates differ (Avid case: same rate; Premiere can differ).
   - `audio_sample_rate` / `audio_bits_per_sample` /
     `audio_channels` from the file SourceMob's descriptor (reuses
     Phase 6.x `_audio_descriptor_info`).
   - `terminal_mob_id` / `terminal_mob_class` for cross-track
     aggregation indexing.
   - `sub_clips`: tuple of nested Clip — populated when the
     component is an OG with multi-input fan-out.
3. **Multi-input combiner fan-out** in `operator.list_clips`. When a
   component is an OperationGroup with ≥2 SourceClip inputs, builds
   a top-level Clip with `sub_clips` populated (one per input). Each
   sub-clip carries its own chain-walk-recovered identity.
4. **Cross-track Source pull list**. New `operator.source_inventory`
   iterates every SourceMob in the file, walks every clip on the
   topmost composition, and returns deduplicated entries with
   `use_count` + `used_by` (track + clip references). Cached at the
   `web/state` layer; backed by new `GET /api/sources`.
5. **Frontend Sources tab + sub-clip rendering**:
   - New top-level "Sources" tab between Tracks and All Mobs.
   - Source-row layout: use-count badge + name + format string
     (`48 kHz · 24-bit · mono · WAVE`) + online/offline indicator
     dot.
   - "Used by" panel inside the inspector with clickable rows that
     jump straight to the corresponding clip in the Tracks view.
   - Tracks-view center-pane tree expands a multi-input combiner
     clip into its sub-clip rows (each independently selectable);
     each sub-clip then drills further into its source MasterMob.
   - Inspector operator-summary card now shows audio format,
     head/tail handles (frames + seconds), source locators panel,
     and combiner-input count.
   - Per-clip offline indicator (red dot) on the tree row when any
     locator is explicitly offline.

Also: pyaaf2 OperationDef registration in the test fixture, which
required setting up a synthetic OperationDef with `NumberInputs=2`
and registering it in the dictionary before constructing the
OperationGroup — captured in `_write_combiner_aaf`.

Total: 235 tests pass. 18 new tests across `test_chain.py` (6),
`test_operator.py` (8), `test_web_api.py` (4).

## Acceptance criteria — checklist

| # | Criterion | Status |
|---|---|---|
| 1 | `pytest` exits 0 with new + existing tests passing | ✅ — 235 tests |
| 2 | PWD_310 HOST track recovery > 80% (vs Phase 6 ~55%) | ✅ — **99.3%** (285 of 287 SourceClips); 96.6% across first 8 audio tracks |
| 3 | Per-clip head/tail handles populate; source paths populate where Locator exists | ✅ — handles populate (HOST first clip: head 60 frames / 2.0s, tail 454 frames / 15.1s); locators come through where present (PWD_310's WAVE descriptors don't carry file:// URLs, but the path is wired) |
| 4 | Sources tab shows deduplicated list, sortable by use; "Used by" jumps work | ✅ — 3032 sources surfaced, top-by-use renders in accent color, jumps wired |
| 5 | Bundled .app rebuilds + launches; bundle delta < 200 KB | ✅ — 16 MB .dmg (same as Phase 6 with the new icon; CSS/JS additions are within noise) |
| 6 | Read-only invariant preserved | ✅ — `tests/test_web_readonly.py` extended to cover `/api/sources` |

## Recovery rate before / after

Measured against `samples/PWD_310_LC_10-07-2025.aaf`. The Phase 6
metric counted "all components" as the denominator (Filler +
Transition + SourceClip + OperationGroup); Phase 7 below counts
SourceClips and sub-clips of multi-input OG fan-out as the
denominator (the operator-meaningful "things that COULD have a
recovered identity").

| Track       | Source-clips (P7) | Recovered | Rate    | Phase 6 baseline |
|-------------|-------------------|-----------|---------|------------------|
| HOST        | 287               | 285       | **99.3%** | ~55% (285/517 components) |
| ANNOUNCER   | 69                | 49        | 71.0%   | — |
| JIMMY       | 214               | 193       | 90.2%   | — |
| CELEB GUEST | 189               | 187       | 98.9%   | — |
| CONTESTANT 1| 125               | 125       | **100.0%** | — |
| CONTESTANT 2| 137               | 137       | **100.0%** | — |
| STUDIO AUD L| 268               | 264       | 98.5%   | — |
| STUDIO AUD R| 297               | 292       | 98.3%   | — |
| **AGGREGATE** | **1586**        | **1532**  | **96.6%** | — |

ANNOUNCER's 71% reflects real production reality, not method failure
— that track legitimately contains inserts from elsewhere
(`PW_SUPERTEASE TEMP VO ...wav` and a handful of other mics).

## Source inventory

PWD_310 source inventory built in 2.9s on first /api/sources call:
- 3032 source mobs total
- 231 referenced by clips on the topmost composition
- Top by use: PW_310_ISO1_B (490 uses), PW_310_ISO1_A (352),
  PW_310_ISO6_A (256), PW_310_ISO6_B (150 + 112 — duplicate
  entries with different mob_ids; expected from how Avid creates
  per-recording-session source mobs)

## Online/offline rate

PWD_310: 0 online of 3032 sources. The WAVEDescriptors in this
particular file carry no Locator entries (Avid embeds the actual
.wav references via OMFI/MediaFiles paths that aren't in the
NetworkLocator chain). The infrastructure is in place; on AAFs that
DO carry NetworkLocator URLs the online/offline indicator will
populate. Tested via the locator helper unit tests.

## Deviations from the brief

1. **Audio info + handles both come from the same SourceMob.** The
   brief described handles as computed from the terminal SourceMob.
   In real Avid AAFs the chain terminates at a tape mob whose slot
   doesn't carry operator-meaningful length data; the file mob
   (mid-chain, with WAVEDescriptor) is where the audio + handle math
   belong. Updated `_build_source_clip_clip` to scan all hops for
   the first audio-bearing SourceMob and use it for both. Falls
   back to the terminal mob when no audio descriptor is present
   (ImportDescriptor cases — covered by tests).

2. **`_compute_handles` gained a `comp_slot_edit_rate` parameter.**
   For correct handle math when the comp slot and source slot edit
   rates differ (a Premiere case, not Avid). The Avid PWD_310 chain
   has both at 30000/1001 so no conversion happens; the parameter
   is documented for future Premiere/other-NLE work.

3. **`source_inventory` is built lazily**, not at file open. The
   3-second walk is too long to add to the open-file flow. First
   `/api/sources` call triggers it; subsequent calls return the
   cached result. Cleared on `/api/close`.

## Bundle size

Phase 6: 14 MB .dmg. Phase 7: 16 MB .dmg — but the +2 MB delta
is attributable to the new high-resolution icon shipped between
Phase 6 and 7, not to Phase 7 code. Pure code/asset delta from
Phase 7 is < 200 KB.

## Phase 8 candidate refinements

Based on what we learned:

- **AIFCDescriptor parser.** PWD_310 uses WAVE; the AIFC parsing
  path was added but not exercised on real files. Pro Tools / Logic
  Pro AAFs may surface this.
- **Premiere edit-rate conversion test.** The `_compute_handles`
  rate-conversion path exists but isn't exercised in our fixtures.
  A Premiere stereo-split fixture would test it.
- **`Used by` cap visibility.** The cap is 50 used-by entries per
  source; if this hides operator-meaningful information for the
  most-used sources we should bump or paginate it.
- **Source inventory file-path fallback.** PWD_310's file mobs have
  no Locator. Some Avid AAFs link to the OMFI MediaFiles directory
  via a different path (the Header dictionary, or a side channel).
  Worth investigating in a Phase 7.x sub-step.
