# AAF Browser — Phase 6 Brief

## Goal

Reorient the web GUI's *front door* from data-geek to post-production
sound operator. Today the default landing is a flat list of every Mob
in the file and navigation follows the AAF object model
(Mobs → Slots → Components → Descriptors). For a sound editor /
engineer / mixer the operative entities are **tracks**, **clips**, and
the **sources** behind those clips — not Mobs and Slots.

Phase 6 adds an **Operator** view as the new default landing. Existing
geek paths (All Mobs, CFB, Find, Walk) stay reachable via secondary
nav and via drill-down from any operator-meaningful object — nothing
the tool currently surfaces is hidden. The right-pane inspector shows
**more**, not less, by adding operator summary fields *on top of* the
existing serializer output.

This phase is the wedge that establishes the operator-first identity.
Subsequent phases build on the same shell with richer workflows
(source pull list, conform triage, AAF diff, full operator summary
fields like handles / sample rate / channel layout, CLI parity).

For project-level context read `docs/PROJECT_OVERVIEW.md` and the
repo-root `CLAUDE.md`. Phases 1–5 (CLI + core, web GUI, chain-walk +
class-filter find, macOS distribution, embedded WKWebView) are
complete — see `docs/completion_reports/`. This brief covers Phase 6
only.

## Why now

Phase 3 shipped the chain-walk, and the corpus-validation work
(`docs/channel-method-corpus-validation.md`) confirmed it works on 50
real-world AAFs across 16 shows — 92.3% ground-truth match on
MatchGame, 100% cross-AAF stability on Password / TKTS / MatchGame
recurring labels. So the *forensic primitive* is solid.

But every operator workflow still requires the user to think like the
AAF format: pick a Mob from a flat list, drill into Slots, find a
Sequence, walk it. That's three concept-shifts away from "track 7,
clip 23, what mic is this?" Phase 6 closes that gap by surfacing the
operator-meaningful entities as the default navigation, with the
chain-walk's recovered mic identity baked into each clip row.

The architecture makes this cheap: the data primitives already exist
in `core/chain.py` and `core/resolver.py`. Phase 6 is mostly a new
`core/operator.py` aggregator + two web endpoints + a frontend
layout pivot.

## Surface design

Three-column layout, **replaces** the current single-pane landing as
default:

1. **Left column — Tracks.** Ordered list of tracks in the open file.
   A "track" is an audio or video slot on the topmost CompositionMob
   (the picture-edit timeline composition). Timecode and data slots
   are filtered out of v1.
   - Per-row: ordinal (PhysicalTrackNumber where set, slot_id
     otherwise), slot name, content kind (audio / video), clip count,
     total duration at the slot's edit rate.
   - Selection drives the center pane.

2. **Center pane — Clips on selected track.** Disclosure-triangle
   tree, same widget pattern as the existing object inspector. Each
   top-level row is one Component on the slot's Sequence (typically a
   SourceClip; sometimes a Filler, OperationGroup, etc.).
   - Top-level row label is operator-meaningful: timeline in/out
     timecode, duration, source MasterMob name, recovered
     recorder/mic identity (from `chain.walk_chain`) if known.
   - Expanding spills out child structure — for a SourceClip, the
     chain hops (SourceClip → MasterMob → SourceMob), plus other
     structural children. Recursively expandable as deep as there is
     content.
   - Any node at any depth is clickable. Click drives the right
     inspector.

3. **Right column — Inspector.** Comprehensive, type-aware detail for
   the currently-selected center-pane node (top-level clip OR any
   expanded child at any depth).
   - For operator-meaningful selections (Clip, chain Hop, MasterMob,
     SourceMob): operator summary fields up top — recovered mic
     identity, source MasterMob/SourceMob name, source file path
     (Locator URL where present) — followed by the raw AAF object
     dump from `aafbrowser.core.aaf.serialize_object()`.
   - For raw AAF objects with no operator overlay: existing
     serialized output unchanged.
   - For CFB streams reached via drill-down: existing hex view.

**Secondary nav** — header tab strip in the topbar:

- **Tracks** (default, new)
- **All Mobs** (current landing — the existing left-pane Mob list)
- **CFB** (existing CFB tab)
- **Find** (existing find panel — promote from bottom drawer to a
  full tab in v1; or keep as bottom drawer if the layout proves
  fiddly)
- **Walk** (existing walk affordance — keep accessible from the
  inspector header on Mob objects, no separate tab needed)

Within each non-Tracks tab the existing UI renders unchanged. The
tab-switch is a layout swap, not a re-implementation.

## Decisions locked in (from planning discussion)

- **Track scope: audio + video.** Hide timecode/data slots in v1. The
  audio operator's primary focus is audio; video is sometimes needed
  as a sync reference. Timecode/data slots are housekeeping and
  noise.
- **Inspector v1: shell + chain-walk-derived fields.** Use what
  `core/chain.py` and `core/resolver.py` already compute — recovered
  mic identity (from chain terminal), source MasterMob name, source
  SourceMob name, source file path (Locator URL). Defer head/tail
  handles, sample rate, channel layout to a follow-up sub-phase
  (they need new descriptor-walking code).
- **Replace mob-list as default.** Tracks is the new front door. All
  Mobs / CFB / Find / Walk live behind secondary nav. We're stating
  the operator-first identity, not hedging it.
- **CLI parity is deferred.** No new CLI commands in v1. The
  `core/operator.py` module is structured so `aafbrowser tracks` /
  `aafbrowser clips` are mechanical to add later.

## Topmost CompositionMob selection

In a clean Avid picture-cut AAF there's typically one obvious
CompositionMob that represents the timeline. Heuristic, in order:

1. If exactly one CompositionMob exists in
   `f.content.compositionmobs()`, use it.
2. Otherwise, pick the CompositionMob with the largest number of
   slots (timelines have many; reference compositions tend to have
   few).
3. If still tied, pick the one with the longest aggregate slot
   duration.
4. If still ambiguous (rare), surface a one-time chooser in the
   left-column header before showing tracks.

Edge case: zero CompositionMobs (essence-only AAFs) → show an empty
Tracks state with a one-line hint pointing at the "All Mobs" tab. No
crash.

## Verified API surface (use this, don't guess)

Confirmed against pyaaf2 1.7.1 and the existing `core/chain.py`
implementation.

### CompositionMob enumeration

```python
# pyaaf2 surfaces classed iterators on f.content:
list(f.content.compositionmobs())   # CompositionMob iterator
list(f.content.mastermobs())        # MasterMob iterator
list(f.content.sourcemobs())        # SourceMob iterator
```

`f.content.mobs` is the unfiltered iterator used elsewhere in the
codebase — prefer the typed iterators above for the operator module
since we need only one Mob class.

### Slot classification (audio / video / timecode / data)

`mob.slots` yields `MobSlot` objects whose `media_kind` attribute
identifies the content type. Exact values to expect (verify in
implementation step 2 against the existing chain fixture):

```python
slot.media_kind        # "Picture" | "Sound" | "Timecode" | "DataEssence" | ...
slot.edit_rate         # AAFRational
slot.slot_id           # int
slot.name              # str (often empty)
slot.segment           # Component
```

Map to v1 `kind`:
- `"Sound"` → `"audio"`
- `"Picture"` → `"video"`
- everything else → filtered out of the v1 Tracks list

If `media_kind` is not directly exposed on a slot, fall back to
slot-class-name introspection (`type(slot).__name__` →
`"TimelineMobSlot"` for both audio and video, but the segment's
descriptor or the mob's slots' `physical_track_type` may help). The
implementation step is responsible for landing on a single working
classifier and adding a unit test against a multi-track fixture.

### Per-slot clip enumeration

A slot's `segment` is typically a `Sequence` with a `.components`
list, but may be a single `SourceClip`/`Filler`/`OperationGroup`
directly. `core/chain.py:_chainable_source_clip` already encodes the
shape recognition. The operator module reuses that logic indirectly
by iterating `segment.components` when the segment is a Sequence,
otherwise treating the segment as a single-element clip list.

### Mic identity (chain terminal)

```python
from aafbrowser.core.chain import walk_chain

hops = walk_chain(handle, source_clip)
# Operator-meaningful identity is the deepest non-cycle, non-broken
# terminal that points at a SourceMob with PhysicalTrackNumber set.
```

The "is this a real recorder source?" filter is the
`is_recorder_source` rule documented in
`docs/identifying-clip-channels.md` (Gotcha 3): terminal hop's
mob_class == "SourceMob" AND terminal_reason == "essence" AND
physical_track_number is not None and > 0. Operator-clip rows tag
this as a boolean so the frontend can de-emphasize non-recorder
clips visually if it wants to.

## Architectural shape

### New module `aafbrowser/core/operator.py`

```python
@dataclass(frozen=True)
class Track:
    ordinal: int                        # PTN where set, else slot_id
    slot_id: int
    name: Optional[str]
    kind: str                           # "audio" | "video"
    edit_rate: Optional[str]            # "num/den"
    length: Optional[int]               # in edit-rate units
    clip_count: int

    def to_dict(self) -> dict[str, Any]: ...

@dataclass(frozen=True)
class Clip:
    index: int                          # position on the slot's Sequence
    component_class: str                # "SourceClip" | "Filler" | "OperationGroup" | ...
    timeline_start: Optional[int]       # in slot edit-rate units
    length: Optional[int]
    source_mob_id: Optional[str]        # MasterMob the SourceClip points at
    source_mob_name: Optional[str]
    source_mob_slot_id: Optional[int]
    mic_identity: Optional[str]         # from chain terminal SourceMob.name
    is_recorder_source: bool            # Gotcha-3 filter result
    terminal_reason: Optional[str]      # for non-recorder terminals
    chain_length: int                   # number of hops walked (1 for non-chainable)

    def to_dict(self) -> dict[str, Any]: ...

def list_tracks(handle) -> list[Track]: ...
def list_clips(handle, slot_id: int) -> list[Clip]: ...
def pick_topmost_composition(handle): ...   # the heuristic above
```

Both dataclasses ship `to_dict()` returning a dict with `_type`
markers (`"operator_track"`, `"operator_clip"`) consistent with the
existing serializer convention. The frontend keys off `_type` to
choose its operator-summary header.

Mic-identity is computed lazily per-clip during `list_clips` by
calling `walk_chain(handle, source_clip)`. Rough cost from Phase 3
corpus: ≤5 hops per clip, sub-millisecond per walk; a 100-clip track
resolves in well under a second. Not caching at the state layer in
v1 — revisit if a single track exceeds ~1000 clips.

### New web endpoints in `aafbrowser/web/app.py`

- `GET /api/tracks` →
  ```json
  {"sha256": "...", "tracks": [Track.to_dict(), ...]}
  ```
- `GET /api/track/clips?slot=<int>` →
  ```json
  {"sha256": "...", "slot_id": 1, "clips": [Clip.to_dict(), ...]}
  ```

Both endpoints follow the existing `state.state_lock()` +
`_require_open` pattern. Errors:
- `409 {"error": "no_file_open"}` when no file
- `400 {"error": "bad_request", "detail": "slot must be int"}` on
  invalid slot
- `404 {"error": "not_found", "detail": "no slot with id=N"}` when
  the slot id doesn't exist on the topmost composition

Each Clip's *deeper* expansion (the SourceClip → MasterMob →
SourceMob hops and structural children) is delivered by calling the
existing `/api/object?path=...` endpoint on demand from the
frontend. No new expansion-tree endpoint needed — inherits the
existing serializer's depth handling and cycle safety.

State-locking: same pattern as existing endpoints. No new fields on
`_State`; the topmost-composition pick is recomputed per request
(cheap on real files; profile and cache only if it becomes hot).

### Frontend (`aafbrowser/web/static/`)

- New 3-column CSS grid in `index.html` and `styles.css` — replaces
  the current 2-column `#layout` grid when the Tracks tab is active.
- Header tab strip in the topbar: Tracks (default), All Mobs, CFB,
  (Find/Walk preserved as today). Tab switching swaps the
  `#layout`'s grid template and shows/hides the relevant panes.
- New `app.js` modules:
  - **Track-list renderer (left)** — hits `/api/tracks` on file open,
    renders the rows, click emits a `track-selected` event.
  - **Clip-tree renderer (center)** — hits
    `/api/track/clips?slot=<id>` on track selection, renders each
    clip as a disclosure-triangle row using a generalization of the
    existing `populateNestedObject` pattern. Disclosure-expand calls
    `/api/object?mob_id=<source_mob_id>` to fetch deeper structure
    and renders it with the same nested-object renderer the geek
    inspector uses.
  - **Inspector renderer (right)** — listens for `node-selected`
    events from the center pane. For operator-meaningful node types
    (`operator_clip`, chain `Hop`, `MasterMob`, `SourceMob`),
    renders an operator-summary header block then defers to the
    existing `renderInspectorObject` for the raw AAF object dump.
    For other node types renders the existing inspector unchanged.
- The existing `selectMob` flow is preserved unchanged behind the
  All Mobs tab.

### What does NOT change

- `core/cfb.py`, `core/aaf.py`, `core/serialize.py`,
  `core/resolver.py`, `core/chain.py` — unchanged.
- All existing CLI commands — unchanged (`aafbrowser tree`, `dump`,
  `cfb`, `inspect`, `find`, `walk`, `web`).
- All existing web endpoints — unchanged.
- All existing tests — unchanged and must keep passing.

## Scope

In:
- `aafbrowser/core/operator.py` with `Track`, `Clip`,
  `list_tracks`, `list_clips`, `pick_topmost_composition`.
- `tests/test_operator.py` covering all of the above against the
  existing `chain_aaf`, `two_mob_aaf`, and a new multi-track audio
  fixture.
- `/api/tracks` and `/api/track/clips` endpoints in `web/app.py`.
- Web-API tests (`tests/test_web_operator_api.py` or extension of
  `tests/test_web_api.py`).
- Read-only invariant test extended to cover the new endpoints.
- Frontend: 3-column layout, header tab strip, track/clip/inspector
  wiring.
- README updates: add a paragraph about the new default Tracks view.
- `docs/PROJECT_OVERVIEW.md` Phase 6 row.
- Phase 6 completion report at
  `docs/completion_reports/phase6-completion-report.md`.

Out (deferred to a later sub-phase or phase):
- Operator-summary inspector fields beyond chain-walk-derived
  (handles, sample rate, channel layout, source-file online/offline
  detection).
- CLI parity (`aafbrowser tracks`, `aafbrowser clips`).
- Source-file pull list across all tracks ("give me everything to
  ask the recordist for").
- Conform triage / sanity report.
- AAF diff (compare two AAFs).
- Operator workflows for Premiere AAFs that require special-cased
  channel recovery (the stereo-split via Mono Audio Pan from
  `docs/premiere-aaf-channel-recovery.md` — useful but distinct).

## Build sequence

One commit per step.

1. **Draft this brief** (the file you're reading).
2. **`core/operator.py` step A**: `Track` dataclass +
   `pick_topmost_composition` heuristic + `list_tracks` (audio/video
   filter, ordinal/PTN logic). Unit tests against `chain_aaf` and a
   new multi-track audio fixture in `conftest.py`.
3. **`core/operator.py` step B**: `Clip` dataclass + `list_clips`
   (chain-walk-derived `mic_identity`, `is_recorder_source`,
   `terminal_reason`). Unit tests against `chain_aaf` and the
   multi-track fixture.
4. **Web endpoints**: add `/api/tracks` and `/api/track/clips` to
   `web/app.py`. Web-API tests in `tests/test_web_api.py` or a new
   `tests/test_web_operator_api.py`. Extend `tests/test_readonly.py`
   to include the new endpoints (web-side; CLI side stays
   unchanged).
5. **Frontend layout**: add the header tab strip and 3-column grid
   to `index.html` + `styles.css`. Tabs work but only Tracks shows
   real content; All Mobs / CFB render their existing UI behind the
   tab switch.
6. **Frontend Tracks pane**: track-list renderer in `app.js`, hits
   `/api/tracks`, click selects a track and triggers the clips
   fetch.
7. **Frontend Clips pane**: clip-tree renderer using the
   disclosure-triangle pattern; expansion drills via
   `/api/object?path=...`; selection emits to the inspector.
8. **Frontend Inspector pane**: operator-summary header for
   `operator_clip` / Mob types, then existing `renderInspectorObject`
   below.
9. **Manual end-to-end** on real samples (Password show iso AAFs,
   MatchGame, CSgameshow); spot-check recovered mic identities
   against the corpus-validation ground truth in
   `docs/channel-method-corpus-validation.md`.
10. **README + PROJECT_OVERVIEW updates**.
11. **Phase 6 completion report** under `docs/completion_reports/`.

## Acceptance criteria

1. `aafbrowser web samples/<any-Password-iso>.aaf` opens with the
   **Tracks** tab active by default. Left column shows the audio +
   video tracks for the topmost CompositionMob.
2. Clicking a track populates the center pane with that track's
   clips. Each clip row shows its operator-meaningful label
   (timeline start, duration, source name, recovered mic identity
   where present).
3. The disclosure triangle on each clip expands to show child
   structure recursively. Any node at any depth is clickable and
   updates the right inspector.
4. The right inspector shows the operator-summary header (mic
   identity, source mob name, source file path) followed by the
   existing serialized AAF object dump for operator-meaningful node
   types.
5. The header tab strip switches to All Mobs / CFB cleanly. Behind
   each non-Tracks tab the existing pre-Phase-6 UI renders
   unchanged.
6. Recovered mic identities for the Phase-3 reference clip in the
   Password sample match the chain-walk corpus-validation results
   (e.g. `PW_310_ISO1_B PTN=1`).
7. Read-only invariant: input file SHA-256 unchanged after
   exercising the new endpoints (verified by an extended
   `test_readonly.py` block on the web side).
8. `pytest` exits 0. New tests cover `core/operator.py` and the new
   web endpoints. No regressions in the existing 8+ test files.
9. `bash packaging/macos/build.sh` still produces a working bundle
   that launches into the new Tracks-default landing inside
   WKWebView. Bundle size delta is ≤ 1 MB (no new dependencies).

## Risks to flag rather than paper over

- **Topmost-CompositionMob heuristic.** In a clean Avid AAF there's
  one obvious composition. In multi-version edits or corrupted
  files, the "biggest by slot count" heuristic may pick the wrong
  one. Mitigation: log the chosen composition's mob_id in the
  `/api/tracks` response so the user can see which one was picked,
  and surface the chooser UI when the heuristic is genuinely
  ambiguous.
- **Slot media-kind classifier.** `slot.media_kind` may not be the
  exact attribute name on every pyaaf2 version. The implementation
  step must verify against `chain_aaf` + a real sample and pick the
  working accessor. If we end up with class-name introspection,
  document why in the code.
- **Clip-tree disclosure performance.** A 500-clip track expanded
  many levels deep could become a lot of DOM. Mitigation: lazy
  expansion (we already do this), and don't auto-expand below the
  first level.
- **Chain-walk per-clip cost.** Per the Phase-3 corpus, sub-ms per
  clip. A pathological 5000-clip track might take a few seconds.
  Mitigation: render the track row immediately, fetch clips on
  selection (already in the design), and show a loading state in
  the center pane.
- **Frontend layout swap on tab change.** Switching the `#layout`
  grid template at runtime is a load-bearing CSS change. Mitigation:
  use CSS classes on `<body>` (e.g. `body.view-tracks`,
  `body.view-mobs`) to drive the grid template; one source of
  truth.
- **Existing find-panel ergonomics.** The find drawer at the bottom
  is currently global. With tabs it could either stay global
  (existing behavior, simplest) or move into a Find tab. The brief
  defers — pick whichever is cleaner during step 5.

## Completion report

When Phase 6 lands, produce
`docs/completion_reports/phase6-completion-report.md` covering:

- Acceptance-criteria checklist with results.
- Any deviations with rationale.
- Heuristic-pick log: which CompositionMob the heuristic chose on
  each tested sample, plus a sentence on whether that matched
  expectation.
- Manual cross-check against the corpus-validation ground-truth
  results for at least one Password sample.
- Bundle size delta (Phase 5: 14 MB → Phase 6: ?).
- A short "what feels different" paragraph: did the Tracks-default
  landing actually shift the felt orientation, or did it just
  rearrange pixels?
- Phase 7 candidate ideas (no commitment): handles + sample rate +
  channel layout in the inspector; source pull list across the
  whole file; conform triage; CLI parity; AAF diff.
