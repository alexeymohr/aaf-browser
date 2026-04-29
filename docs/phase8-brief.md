# AAF Browser — Phase 8 Brief

## Goal

Apply the Premiere-specific recovery rules from
`docs/premiere-aaf-channel-recovery.md`. Stereo splits become
recoverable (high-confidence via Mono Audio Pan + `_L`/`_R` suffix);
multichannel polywav imports become explicitly *labelled*
unrecoverable instead of silently producing meaningless mic identity
strings.

For project-level context read `docs/PROJECT_OVERVIEW.md` and the
repo-root `CLAUDE.md`. Phases 1–7 are complete — see
`docs/completion_reports/`. This brief covers Phase 8 only.

## Why now

Phase 6 + 7 nailed the Avid story (96.6% mic recovery on PWD_310
across 8 audio tracks). The corpus validation flagged Premiere as
the next format-specific work: 4 Premiere AAFs (CasaLuxe + SavingJones)
were inspected forensically. The result is a clear bifurcation:

- **Stereo splits** (`Audio N_L` / `Audio N_R` tracks): channel
  identity is encoded redundantly — once in the SourceMob name
  suffix and once in a track-level **Mono Audio Pan** OperationGroup
  parameter (0.0 = L, 1.0 = R). 100% recoverable across all 54
  stereo-paired tracks in the corpus.
- **Multichannel polywav imports** (single polywav → multiple `Audio
  N` tracks): channel identity destroyed at import. 15 plausible
  metadata locations confirmed empty. Tool currently produces a
  random-looking mob_id-tail "identity" that's misleading; we
  should explicitly mark these as unrecoverable.

Two of three signals to add — authoring detection (lightweight),
Premiere stereo-split recovery (specific code path), polywav
non-recovery marker (specific code path). All ship together since
they share the authoring-kind detection helper.

## Scope

**In:**

1. **Authoring-driven format dispatch.**
   - `operator.detect_authoring_kind(handle) -> str` returning
     `"avid"` / `"premiere"` / `"protools"` / `"unknown"`. Uses the
     `Header.IdentificationList` ProductName surfaced in Phase 6.x.
   - `SessionSummary.authoring` already carries `product_name` and
     `company_name`; add a normalized `kind` field for downstream
     branching.
2. **Premiere stereo-split recovery via Mono Audio Pan.**
   - New `_track_pan_channel(slot_segment) -> Optional[str]` reads
     the slot-level OperationGroup's Operation name and
     Parameters[0].Value. Returns "L" if Pan ≈ 0.0, "R" if Pan ≈
     1.0, None otherwise.
   - `Track` dataclass gains `pan_channel: Optional[str]` set
     during `list_tracks` for Premiere AAFs (silent on Avid).
   - `Clip` dataclass gains `recovery_status: str` ("recoverable"
     / "ambiguous" / "unrecoverable") and `recovery_method:
     Optional[str]` (free-text).
   - Premiere clips on a `pan_channel`-bearing track get
     `recovery_status="recoverable"` and
     `recovery_method="premiere_stereo_split"`.
3. **Premiere multichannel polywav detection.**
   - For Premiere AAFs, when none of the recovery paths apply (no
     Pan, no `_L`/`_R` suffix on terminal SourceMob name, terminal
     SourceMob's descriptor is ImportDescriptor with no Locator
     channel info), mark as `recovery_status="unrecoverable"`
     with `recovery_method="premiere_polywav_indeterminate"`.
4. **Frontend rendering of recovery_status.**
   - Clip row visual variant: faint red border-left for
     unrecoverable, default accent-warm for recorder-source
     recoverable.
   - Inspector summary card adds two rows: "Recovery status" and
     "Recovery method".

**Out (deferred):**

- Conform / diff / pull list (already shipped or excluded).
- AIFCDescriptor parsing improvements (Phase 7 follow-up).
- Track-placement-convention heuristic for polywav (the doc's
  workaround #1 — not pursued; too fragile).
- Audio cross-correlation for polywav (workaround #2 — out of
  scope, requires external file access).
- Other NLE format-specific recoveries (Resolve, Final Cut Pro X
  XML imports — different format entirely).
- Pro Tools authoring detection (gets the kind label but no
  special-case recovery path; Pro Tools-authored AAFs follow the
  Avid chain pattern).

## Decisions to lock in

- **Authoring detection is fuzzy.** Match `ProductName` by
  case-insensitive substring: "avid" → "avid", "premiere" →
  "premiere", "pro tools" / "protools" → "protools". Anything
  else → "unknown". The pattern handles version-suffix variants
  ("Avid Media Composer 24.12.1", "Adobe Premiere Pro 2024", etc.)
  without enumerating every product version.

- **Pan parameter is read with tolerance.** AAF Mono Audio Pan
  param is an AAFRational. Per the doc, 0.0 = LEFT, 1.0 = RIGHT.
  Treat values within 0.1 of the endpoints as L/R; anything in
  between (a real pan position other than hard L/R) → None
  (no channel hint).

- **`recovery_status` defaults to "recoverable" for Avid clips
  with `is_recorder_source=True`**, "unrecoverable" for others
  (to match Phase 7 behavior). Premiere paths add new states.

- **Stereo-split detection prefers the Pan parameter, falls back
  to `_L`/`_R` suffix on the SourceMob name.** Both signals exist
  in real Premiere AAFs; the Pan path is more reliable but the
  suffix is enough when the chain doesn't surface the Pan OG.

## Verified API surface (use this, don't guess)

These are confirmed against pyaaf2 1.7.1 and the existing Phase 6+7
operator/chain modules.

### Authoring info (already exposed)

```python
# Phase 6.x already populates these on the SessionSummary:
authoring.product_name = "Avid Media Composer 24.12.1"
authoring.company_name = "Avid Technology, Inc."
```

Phase 8 adds a normalized `kind` field, derived from
`product_name` via case-insensitive substring matching.

### Mono Audio Pan parameter on a track-level OperationGroup

Per the Premiere doc:

```python
slot.segment              # OperationGroup
  .operation              # OperationDef with name "Mono Audio Pan"
  ["Parameters"].value    # list of TaggedValue or VaryingValue
    [0].value             # AAFRational(num, denom) where num/denom ∈ {0.0, 1.0}
  ["InputSegments"].value
    [0]                   # Sequence — the actual clips (same as Avid pattern)
```

The Operation name lookup needs `op_def.name == "Mono Audio Pan"`.

The pan value access depends on parameter type:
- `ConstantValue.value` for constant pans (the common case)
- `VaryingValue` (with PointList) for automated pans — Phase 8
  reads only the first point's value as an approximation; full
  automation handling is deferred.

### Reused (no change)

- `chain.walk_chain` — unchanged from Phase 6.
- `chain.walk_chain_tree` — unchanged from Phase 7.
- `operator._unwrap_operation_group` — single-input unwrap.
- `operator._operation_group_inputs` (also `chain._operation_group_input_segments`).
- The Phase 7 `Clip` dataclass — gains two new fields
  (`recovery_status`, `recovery_method`), no other changes.

## Architectural shape

### Backend changes

- `core/operator.py`:
  - **NEW** `detect_authoring_kind(handle) -> str`.
  - `AuthoringInfo` dataclass gains `kind: str`.
  - `_build_authoring_info` populates `kind` via the new helper.
  - **NEW** `_track_pan_channel(slot_segment, op_def_name="Mono Audio Pan")
    -> Optional[str]` — returns "L"/"R"/None.
  - `Track` dataclass gains `pan_channel: Optional[str]`.
  - `list_tracks` populates `pan_channel` from
    `_track_pan_channel(slot.segment)` (silent for non-Premiere
    AAFs since the Pan OG won't be present).
  - `Clip` dataclass gains:
    - `recovery_status: str` (default "recoverable" for Avid recorder
      sources, "unrecoverable" for everything else).
    - `recovery_method: Optional[str]` (free-text label).
  - `_build_source_clip_clip` accepts the track's `pan_channel`
    (passed via list_clips) and the file's `authoring_kind` to
    decide recovery_status:
    - If pan_channel set + Premiere → "recoverable",
      method="premiere_stereo_split".
    - If terminal SourceMob name ends in `_L`/`_R` + Premiere →
      "recoverable", method="premiere_name_suffix".
    - If Premiere + no recovery signal + name looks like polywav →
      "unrecoverable", method="premiere_polywav_indeterminate".
    - If Avid + is_recorder_source → "recoverable",
      method="avid_chain_walk".
    - Else → "unrecoverable" or "ambiguous" depending on what
      hops surfaced.
  - `list_clips` passes the track's pan_channel + authoring_kind
    to each `_build_source_clip_clip` call.
  - `list_tracks` and `list_clips` both stop being silent on the
    file's authoring kind — fetched once at the call entry via
    `detect_authoring_kind(handle)` and threaded through.

- `web/app.py`:
  - No new endpoints. `Track.to_dict()` and `Clip.to_dict()`
    automatically grow the new fields. `SessionSummary.to_dict()`
    automatically grows `authoring.kind`.

### Frontend changes

- `static/styles.css`:
  - `.tree-row.kind-clip-unrecoverable` variant: faint red
    border-left, muted text.
  - Track row pan-channel suffix styling.

- `static/app.js`:
  - `renderClipRowContent`: append a small "(L)" / "(R)" suffix
    when the clip's pan_channel is set; apply unrecoverable
    styling when `recovery_status === "unrecoverable"`.
  - `operatorSummaryFor`: add "Recovery status" + "Recovery
    method" rows.
  - `renderTrackList`: append a small `[L/R]` indicator on
    Premiere stereo-split tracks.
  - Session bar: the existing "Authored by" cell already shows
    ProductName; no change needed (kind is implicit).

### What does NOT change

- `core/cfb.py`, `core/aaf.py`, `core/serialize.py`,
  `core/resolver.py`, `core/chain.py` — unchanged.
- All existing CLI commands — unchanged.
- All existing web endpoints — unchanged shape; new fields on
  Track / Clip / Authoring dicts.
- Avid-AAF behavior — preserved exactly.

## Files to be modified / created

- `aafbrowser/core/operator.py` (modified)
- `aafbrowser/web/static/styles.css` (modified)
- `aafbrowser/web/static/app.js` (modified)
- `tests/conftest.py` (modified — Premiere stereo-split fixture +
  polywav-style fixture)
- `tests/test_operator.py` (modified)
- **NEW** `docs/phase8-brief.md` (this file)
- **NEW** `docs/completion_reports/phase8-completion-report.md`
- `docs/PROJECT_OVERVIEW.md` (modified — Phase 8 row)
- `README.md` (modified — Premiere recovery paragraph)

## Build sequence

1. **This brief** (`docs/phase8-brief.md`).
2. `detect_authoring_kind` + `AuthoringInfo.kind` + tests.
3. Synthetic Premiere fixtures in conftest.py:
   - `premiere_stereo_split_aaf`: a CompositionMob with two slots,
     each slot.segment is a Mono Audio Pan OperationGroup wrapping
     a Sequence containing SourceClip → MasterMob "Audio 1_L"
     (slot 0) and "Audio 1_R" (slot 1). Pan = 0.0 / 1.0
     respectively.
   - `premiere_polywav_aaf`: a CompositionMob with two slots,
     each pointing at MasterMobs "Audio 1" and "Audio 2" with
     identical metadata (no `_L`/`_R`, no Pan, no PTN).
   - Both fixtures register their authoring info as Premiere via
     Header.IdentificationList.
4. `_track_pan_channel` + `Track.pan_channel` + tests.
5. `Clip.recovery_status` + `recovery_method` + per-format detection
   logic + tests against all fixtures (Avid + Premiere stereo-split
   + Premiere polywav).
6. Frontend rendering: clip-row suffix, recovery_status visual
   variant, inspector rows.
7. README + PROJECT_OVERVIEW updates.
8. Phase 8 completion report.

## Acceptance criteria

1. `pytest` exits 0 with new + existing tests passing.
2. On the synthetic Premiere stereo-split fixture: each track
   reports `pan_channel="L"` / `"R"`; each clip reports
   `recovery_status="recoverable"` and
   `recovery_method="premiere_stereo_split"`.
3. On the synthetic Premiere polywav fixture: clips report
   `recovery_status="unrecoverable"` with
   `recovery_method="premiere_polywav_indeterminate"`.
4. Existing PWD_310 (Avid) recovery rates from Phase 7 unchanged
   (HOST 99.3%, aggregate 96.6%).
5. The bundled `.app` rebuilds + launches; UI surfaces L/R
   suffixes on track rows and recovery_status variants on clip
   rows.
6. Read-only invariant preserved.

## Risks to flag rather than paper over

- **No real Premiere AAFs in `samples/`.** Synthetic fixtures only
  mirror the documented patterns; they don't exercise the variations
  the corpus discovered. We may need a Phase 8.x cross-check pass
  once a real Premiere AAF is available locally.
- **Mono Audio Pan on automated pans.** The fixture uses
  ConstantValue (the common case). VaryingValue parameters with
  per-clip pan animation aren't covered; the implementation reads
  only the first point as an approximation. Document.
- **Name-suffix heuristic false positives.** Avid sources sometimes
  legitimately end in `_L`/`_R` (a user-typed naming convention).
  Restricting the name-suffix path to Premiere AAFs (via
  authoring.kind) keeps Avid flow unchanged.
- **OperationDef registration in fixtures.** Same hurdle as Phase 7's
  `combiner_aaf` — Premiere fixtures need their own
  "Mono Audio Pan" OperationDef registered before constructing the
  OG. Documented in conftest.

## Completion report

When Phase 8 lands, produce
`docs/completion_reports/phase8-completion-report.md` covering:

- Acceptance-criteria checklist.
- Authoring detection results on `samples/PWD_310_*` (should be
  "avid").
- Confirmation that PWD_310's Phase 7 recovery numbers are
  unchanged (regression check on the Avid path).
- A note on what real-Premiere validation would look like once
  samples are available.
- Phase 9 readiness check (CLI parity + notarization is the next
  shipped phase).
