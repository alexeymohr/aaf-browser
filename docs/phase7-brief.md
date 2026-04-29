# AAF Browser — Phase 7 Brief

## Goal

Lift mic-identity recovery on real Avid sessions from the Phase 6
baseline (~55% on PWD_310's HOST track) to ~85–90% by recursing into
multi-input OperationGroup combiners. Add the per-clip operator info
that the Phase 6 brief deferred — source file path, online/offline
status, head/tail handles, per-clip audio specs — and surface a
cross-track **Sources** pull list as a new top-level view.

This phase is about *recovery depth* and *workflow breadth*. The
operator-first surface from Phase 6 is the right shape; this phase
makes it answer more questions on more clips.

For project-level context read `docs/PROJECT_OVERVIEW.md` and the
repo-root `CLAUDE.md`. Phases 1–6 are complete — see
`docs/completion_reports/`. This brief covers Phase 7 only.

## Why these four bundled together

Phase 6 shipped the operator surface and stopped at "what's the
recorder mic for this clip?" — a single-input chain walk. Real-world
Avid sessions use OperationGroups extensively: per-clip audio level
automation (single-input, already handled in Phase 6.x), but also
multi-input audio mix-downs (a stereo bus combining two mono mics, a
backup feed combiner, etc.). The Phase 6 corpus validation said ~30%
of clips on real Avid AAFs terminate at these multi-input combiners
with `terminal_reason="operation_group"` and no recovered identity.

Once we're recursing into combiners, the per-clip metadata story
becomes more complete: each leaf SourceClip in the recursion has its
own source mob, its own essence file path, its own audio format. The
inspector already accepts an arbitrary nested structure (the Phase 6
disclosure tree); surfacing a list of sub-clips per top-level clip is
a natural extension of that pattern.

Source file path + online/offline detection is the missing "where's
this from?" answer that operators need to triage delivery. It uses
the same SourceMob descriptor walk we already do for the audio
summary (Phase 6.x). Same Locator extraction primitive can drive a
cross-track pull list — give an assistant a deduplicated list of
every essence file the AAF references and which ones are missing.

Head/tail handles are pure arithmetic on data we already have in the
chain hops. Cheap.

Per-clip audio specs flag anomalies (a 96k insert in a 48k session,
a stereo file mistakenly used as mono, etc.) that the session-bar
uniform values mask.

## Scope

**In:**

1. **Multi-input OperationGroup sub-walk** in `core/chain.py` and
   `core/operator.py`.
2. **Source file path + online/offline** per clip via the terminal
   SourceMob's Locator entries.
3. **Head/tail handle** (frames + seconds) per clip.
4. **Per-clip audio specs** (sample rate / bit depth / channels)
   from the terminal SourceMob's descriptor.
5. **Sources tab** — new top-level view showing the deduplicated
   list of source mobs across all tracks, with use-count, locator
   path preview, online/offline status, and a click-through that
   shows every clip on every track that uses the selected source.

**Out (deferred to a later phase):**

- Conform / diff / triage workflows (deferred per user direction).
- CLI parity (Phase 9).
- Premiere-aware recovery (Phase 8).
- Notarization (Phase 9).
- Documentation walkthroughs (deferred per user direction).
- Anything that requires writing to AAFs (read-only invariant
  unchanged).

## Decisions to lock in

- **Sub-walk shape: tree, not flat.** A multi-input OperationGroup
  produces a `HopBranch` containing N sub-chains, one per input.
  Existing single-input chains stay flat lists. The chain module
  ships `walk_chain_tree(...)` as a sibling function so existing
  callers (and the brief's "doesn't change core/chain.py" Phase 6
  promise) stay backward compatible. `walk_chain` itself is
  unchanged.

- **`Clip.sub_clips` carries the recursion result.** Top-level Clip
  for a SourceClip with a multi-input combiner terminal:
  `mic_identity = None` (no single answer), `sub_clips = [Clip,
  Clip, ...]` each with their own recovered identity. UI shows the
  top-level clip with a "(N inputs)" hint and lets the user expand
  to see each input's mic.

- **Source path online check is local-only.** `urlparse(url).scheme
  in ("file", "")` plus `os.path.exists`. Network locators are
  reported with `online=None` (unknown) rather than False —
  could-be-mounted-eventually is a real case.

- **Head/tail handles in source edit-rate units.** With seconds
  derived if the source slot's `edit_rate` is known. Don't try to
  reconcile timeline vs source edit rate — they often match in
  practice but the math gets weird when they don't (e.g.
  pull-down). Surface raw + seconds; let the operator interpret.

- **Sources tab is a peer of Tracks / All Mobs / CFB.** Same
  `view-tabs` strip; same shared inspector. 2-column layout
  (sources list left, inspector right). Clicking a source row
  populates the inspector with the SourceMob's full object dump
  PLUS a "Used by" list of every (track, clip) reference.

- **Source inventory is built once at file open.** Cached at the
  `web/state` layer like the existing `mob_index`. Iterates every
  source mob; ~3000 on PWD_310 should be sub-second.

## Verified API surface (use this, don't guess)

These are confirmed against pyaaf2 1.7.1 and the existing Phase 6
operator/chain modules.

### OperationGroup.InputSegments

`OperationGroup` doesn't expose `input_segments` as a Python
attribute. Access via property iteration:

```python
inputs = list(og["InputSegments"].value)
```

(See `operator._operation_group_inputs(og)` from Phase 6.x — reuse
this helper.)

### SourceMob descriptors and Locators

Each SourceMob has a single `.descriptor`. Audio descriptors
(`WAVEDescriptor`, `PCMDescriptor`, `AIFCDescriptor`,
`ImportDescriptor`) all carry an optional `Locator` property — a
StrongRefVector of `NetworkLocator` (URLString property = `file://`
URL) or `TextLocator` (Name property = free-text path).

```python
locators = list(desc["Locator"].value)
for loc in locators:
    cls = type(loc).__name__
    if cls == "NetworkLocator":
        url = loc["URLString"].value  # 'file:///Volumes/.../file.wav'
    elif cls == "TextLocator":
        url = loc["Name"].value
```

Some SourceMobs have no Locator. Some carry stale paths. Surface
what's there; let the user decide what to trust.

### SourceMob slot length for handle math

For a SourceClip pointing at a SourceMob slot:

```python
sm_slot = source_mob.slot_at(slot_id)
total_source_length = sm_slot.segment.length  # in source edit-rate
clip_start = source_clip.start                 # head handle
clip_end   = clip_start + source_clip.length
tail_handle = total_source_length - clip_end
```

Source slot edit rate available via `sm_slot.edit_rate` (an
AAFRational).

## Architectural shape

### Backend

- `core/chain.py`:
  - **NEW** `HopBranch` dataclass: `{combiner_class: str,
    operation_def_name: str | None, inputs: list[list[Hop |
    HopBranch]]}`. Each input is its own walk result (recursive).
  - **NEW** `walk_chain_tree(handle, start, *, slot_id=None,
    max_hops=64, max_depth=8) -> list[Hop | HopBranch]`. Runs the
    same flat walk as `walk_chain`; on a terminal that's an
    OperationGroup with ≥2 SourceClip inputs, replaces the
    terminal Hop with a HopBranch and recurses on each input.
    Cycle-safe via a shared visited set across all branches.
    `max_depth` bounds recursion through nested combiners.
  - `walk_chain` itself unchanged (Phase 6 promise).

- `core/operator.py`:
  - `Clip` dataclass gains:
    - `sub_clips: list["Clip"]` — recursive structure for combiner
      terminals; empty for normal single-input clips.
    - `source_locators: list[dict]` — `[{"url", "kind": "network|text",
      "online": bool|None}]`.
    - `head_handle_frames: int | None`,
      `tail_handle_frames: int | None`,
      `head_handle_seconds: float | None`,
      `tail_handle_seconds: float | None`.
    - `audio_sample_rate: str | None` (e.g. "48000/1"),
      `audio_bits_per_sample: int | None`,
      `audio_channels: int | None`.
  - `_build_source_clip_clip` updated to compute all of the above.
    On combiner terminal: calls `walk_chain_tree`, builds a Clip
    per input via the same `_build_source_clip_clip` (recursive),
    and stashes them in `sub_clips`.
  - **NEW** `source_inventory(handle) -> list[SourceInventoryEntry]`:
    deduplicated list of (mob_id, name, descriptor_summary,
    locators, clip_uses) across the topmost composition. Entry's
    `clip_uses` is `[(track_slot_id, track_name, clip_index,
    timeline_start)]`.
  - **NEW** `_collect_locators(desc) -> list[dict]` helper.
  - **NEW** `_compute_handles(source_clip, terminal_hop) ->
    (head_frames, tail_frames, head_seconds, tail_seconds)`.

- `web/app.py`:
  - **NEW** `GET /api/sources` returning `{"sha256", "sources":
    [...]}`. Same state-lock pattern.
  - Existing `/api/track/clips` Clip dicts gain the new fields
    automatically via `Clip.to_dict()`.

- `web/state.py`:
  - `_State` gains `source_inventory: list[dict] | None` (built
    once at `open_file`, cleared on close).
  - `_build_source_inventory(handle) -> list[dict]` helper at
    module scope.

### Frontend

- `static/index.html`:
  - Add `<button class="view-tab" data-view="sources">Sources</button>`.
  - Add `<aside id="sources-pane" class="left-pane view-sources-only">
       <div class="filter-row">
         <input id="source-filter" type="text" placeholder="filter by name…">
       </div>
       <div id="source-list" class="scroll-list"></div>
     </aside>`.

- `static/styles.css`:
  - `body.view-sources #layout` grid template
    (`var(--col-sources) var(--splitter) minmax(0, 1fr)`); add
    `--col-sources: 480px` to `:root`.
  - Source-row styling: name + use-count badge + online/offline
    indicator (small colored dot).
  - Clip-row variant: faint red border-left for offline clips.

- `static/app.js`:
  - `loadSources()` + `renderSourceList()` + `selectSource()`.
  - `renderClipRowContent`: add online/offline indicator when the
    clip's terminal source has a locator.
  - `operatorSummaryFor(clip)`: add rows for source path, online
    status, head/tail handles (frames + seconds), per-clip audio
    specs.
  - Sub-clips rendering: when expanding a top-level clip in the
    center-pane tree, the first level of children is one row per
    `sub_clip` (each clickable, each with its own recovered mic).
    Below that, the standard AAF-object tree continues per clip.
  - Selecting a source in the Sources view: inspector shows the
    SourceMob full object dump + a "Used by" list (clickable rows
    that jump to the corresponding clip in the Tracks view).

### What does NOT change

- `core/cfb.py`, `core/aaf.py`, `core/serialize.py`,
  `core/resolver.py` — unchanged.
- `core/chain.walk_chain` (the Phase-6 entry) — unchanged.
- All existing CLI commands — unchanged (CLI parity is Phase 9).
- All existing web endpoints — unchanged shape; `/api/track/clips`
  Clip dicts grow new fields but old ones are preserved.
- All existing tests — unchanged and must keep passing.

## Files to be modified / created

- `aafbrowser/core/chain.py` (modified — `walk_chain_tree`,
  `HopBranch`)
- `aafbrowser/core/operator.py` (modified — Clip new fields,
  source_inventory, helpers)
- `aafbrowser/web/app.py` (modified — `/api/sources`)
- `aafbrowser/web/state.py` (modified — cached source_inventory)
- `aafbrowser/web/static/index.html` (modified — Sources tab)
- `aafbrowser/web/static/styles.css` (modified — Sources view +
  online/offline indicators + sub-clip rendering)
- `aafbrowser/web/static/app.js` (modified — Sources view, sub-clip
  rendering, expanded inspector)
- `tests/conftest.py` (modified — combiner_aaf fixture +
  source-locator fixture)
- `tests/test_chain.py` (modified — `walk_chain_tree` tests)
- `tests/test_operator.py` (modified — new Clip fields,
  source_inventory)
- `tests/test_web_api.py` (modified — `/api/sources` shape +
  /api/track/clips new fields)
- `tests/test_web_readonly.py` (modified — extended endpoint
  coverage)
- **NEW** `docs/phase7-brief.md` (this file)
- **NEW** `docs/completion_reports/phase7-completion-report.md`
- `docs/PROJECT_OVERVIEW.md` (modified — Phase 7 row)
- `README.md` (modified — Sources tab paragraph)

## Build sequence

One commit per step (or grouped where the steps form a single
logical unit).

1. **This brief** (`docs/phase7-brief.md`).
2. **`core/chain.walk_chain_tree`** + `HopBranch` dataclass +
   tests on a new `combiner_aaf` fixture in conftest. Cycle
   safety verified via existing `cycle_aaf`.
3. **`core/operator` Clip extensions** — sub_clips,
   source_locators, head/tail handles, per-clip audio specs,
   chain-tree integration. Tests for each new field.
4. **`source_inventory` + `state.py` cache + `/api/sources`** +
   API tests + read-only extension.
5. **Frontend Sources view** — HTML + CSS + JS for the new tab,
   the source-list renderer, the inspector "Used by" panel.
6. **Frontend per-clip rendering** — operator-summary additions,
   online/offline indicators, sub-clip rendering in the
   center-pane tree.
7. **Manual cross-check on PWD_310**: measure HOST-track recovery
   rate before/after (should hit > 80% mic_identity).
8. **README + PROJECT_OVERVIEW updates**.
9. **Phase 7 completion report**.

## Acceptance criteria

1. `pytest` exits 0; new tests cover walk_chain_tree, the new
   Clip fields, source_inventory, /api/sources, and the read-only
   invariant on the new endpoint.
2. On `samples/PWD_310_LC_10-07-2025.aaf`, the HOST track's
   recovery rate (recorder-source mic identities) is > 80% (vs
   Phase 6's ~55%). Per-clip head/tail handles populate; source
   paths populate where a NetworkLocator exists.
3. Sources tab shows the deduplicated list of source mobs, sortable
   by use count. Selecting a source updates the inspector with the
   "Used by" list; clicking a "Used by" entry jumps to that clip
   in the Tracks view.
4. The bundled `.app` rebuilds, launches, and exhibits all of the
   above. Bundle size delta < 200 KB (no new dependencies).
5. Read-only invariant preserved end-to-end.

## Risks to flag rather than paper over

- **walk_chain_tree explosion on pathological combiners.** A
  combiner with hundreds of inputs, each chain itself going through
  more combiners, could produce a huge tree. Mitigations:
  `max_depth` bound (default 8) and `max_combiner_inputs` bound
  (default 32). Beyond that, surface a "(truncated, N more inputs)"
  marker rather than recursing further.
- **HopBranch shape vs serializer.** The existing `Hop.to_dict()`
  is a simple flat dict. `HopBranch.to_dict()` is recursive; the
  consumers (operator.py, web frontend) need to handle either
  shape. Test both directions.
- **Source-mob descriptor variance.** Avid uses WAVEDescriptor; Pro
  Tools may use AIFCDescriptor; Premiere uses a mix. The Phase 6.x
  `_audio_descriptor_info` only handles WAVE + PCM. For Phase 7
  we want at minimum AIFC support too — extend the helper, add
  test coverage.
- **Locator URL forms.** `file:///Volumes/x/y.wav` and bare
  `/Volumes/x/y.wav` and `urn:smpte:...` (UMID, not a path) all
  need clean handling. Don't os.path.exists a URN.
- **state.source_inventory cost on huge files.** PWD_310 has 3032
  source mobs; iterating and building inventory should be
  sub-second but worth profiling. If it bloats /api/open, defer to
  a lazy-on-first-/api/sources-request approach.
- **Sub-clips in the center-pane tree.** The existing tree-renderer
  assumes children come from `/api/object?mob_id=...` fetches.
  Sub-clips are operator-layer constructs, not AAF objects — the
  renderer needs a small branch to handle "kind=clip" children
  alongside "kind=object" children.

## Completion report

When Phase 7 lands, produce
`docs/completion_reports/phase7-completion-report.md` covering:

- Acceptance-criteria checklist.
- Recovery-rate before/after on PWD_310 HOST track + at least one
  other track.
- Source inventory size and dedup ratio.
- Online/offline rate on the sample (how many sources have
  resolvable file:// URLs that exist on disk vs not).
- Bundle size delta.
- Phase 8 candidate refinements based on what we learn (e.g. if
  AIFCDescriptor needs a different Locator path, document for
  Phase 8 startup).
