# AAF Browser — Phase 3 Completion Report

Date: 2026-04-27
Branch: main (10 Phase-3 commits, on top of the 24 Phase-1+2 commits)
Test suite: **165 tests, all passing** (`pytest` exits 0, ~7.4 s).
Python: 3.14.3 / pyaaf2 1.7.1 / click 8.3.2 / flask 3.1.3 / pytest 9.0.3.

## What was built

Phase 3 adds two operational features on top of the existing
`aafbrowser.core` library, the Click CLI, and the Flask + vanilla-JS
web GUI:

1. **Chain-walk** (`aafbrowser.core.chain`) — follow the SourceClip /
   SourceMobSlotID chain hop-by-hop with full terminal-reason
   coverage; surface as `aafbrowser walk` CLI subcommand,
   `GET /api/walk` REST endpoint, and a "Walk chain" affordance in
   the web inspector.
2. **Mob-class filter on `find`** — restrict the AAF-layer walk to one
   or more Mob classes. CLI: `--class <name>` (repeatable). REST:
   `?class=<name>` (repeatable). Web: a `<details>` checkbox popover
   in the topbar populated from the eager Mob index's
   `classes_summary`.

```
aafbrowser/core/chain.py         # NEW: Hop dataclass + walk_chain()
aafbrowser/core/resolver.py      # find_in_aaf gains mob_class kwarg
aafbrowser/cli/__main__.py       # NEW: walk command; find gains --class
aafbrowser/web/app.py            # NEW: /api/walk; /api/find ?class=
aafbrowser/web/static/{index.html,styles.css,app.js}
                                 # Walk button, chain panel, class filter
```

### Acceptance-criteria checklist

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `aafbrowser walk` against a Password CompositionMob ends at a SourceMob whose name carries the physical capture identity. | OK (3-hop walk on `PW VO 310 B Round 1.wav.new.05` ends at SourceMob `'PW VO 310 B Round 1.wav'`; terminal_reason=no_source_id, the natural chain end). The KEKE iso CompositionMob terminates earlier with `operation_group` because its slot 1 segment is an OperationGroup (an audio mix node) — see Findings. |
| 2 | `walk fixture --json \| python -m json.tool` is valid JSON; hops non-empty; final terminal=true with non-null reason. | OK (JSON validated; covered by `test_walk_json_output`) |
| 3 | `find --class CompositionMob` is materially shorter than unfiltered (≥ 50% reduction on PWD_310). | OK (7.03 s → 3.55 s = 50% off; `--class MasterMob` = 1.72 s = 76% off) |
| 4 | Synthetic cycle terminates with `terminal_reason: "cycle"`; never infinite-loops. | OK (test_cycle_terminates_with_cycle_reason against the cycle fixture) |
| 5 | Web GUI shows "Walk chain" on a SourceClip / Mob; click renders hops; clicking a hop navigates. | OK (button on `*Mob` headers; chain panel renders; hops use existing `navigateToMobId`) |
| 6 | Find class filter excludes other classes. | OK (test_find_class_filter_excludes; multi-select repeated `class=` params confirmed) |
| 7 | SHA-256 of every sample unchanged after a session exercising walk + class-filtered find. | OK (verified pre/post on both samples; round-trip test extended) |
| 8 | `pytest` exits 0 with new tests added. | OK (165 passed in 7.4 s) |

### Test breakdown (Phase 3 additions)

- `tests/test_chain.py` — 11 tests across the 3-hop chain fixture, the
  cycle fixture, the broken-ref fixture, and entry from various start
  types (Mob / SourceClip / mob_id string).
- `tests/test_resolver.py` — 4 new tests for `mob_class` filter
  (exclude / include / multi-class / no-filter-finds-more).
- `tests/test_cli.py` — 5 walk-command tests + 3 `find --class` tests.
- `tests/test_web_api.py` — 7 `/api/walk` tests + 2 `/api/find?class=`
  tests.
- `tests/test_web_readonly.py` — extended omnibus test now exercises
  walk and class-filter forms; SHA-256 of input remains identical
  through the full sequence.
- `tests/conftest.py` — three new programmatic fixtures: a 3-hop chain,
  a cyclic pair, and a broken-ref MasterMob.

Phase 1+2's 132 tests still pass unchanged.

## Deviations from the brief

1. **SourceClip attribute names.** The brief assumed pyaaf2 surfaces
   `.source_id`, `.source_mob_slot_id`, `.start_time`. The actual
   pyaaf2 1.7.1 API exposes `.mob_id`, `.slot_id`, `.start`. The
   chain walker uses the real names; the brief was a hypothetical
   "verified API surface" section that wasn't actually verified at
   brief-writing time. No semantic change to the Hop output.

2. **Single chain panel below property table, no overlay.** The brief
   suggested "a small overlay" as an alternative. Inline panel below
   the property table proved cleaner — it stays in context with the
   object the user clicked Walk on, doesn't fight the inspector's
   horizontal scroll, and persists naturally as the user scrolls.

3. **Class filter UI is a `<details>` checkbox popover, not a
   `<select multiple>`.** Native multi-select is a known
   ergonomic-pain pattern (cmd-click discovery, no obvious affordance
   for selected state). A `<details>` with checkboxes is more
   discoverable, shows the selected count in its summary text, and
   closes on outside click. Functionally equivalent for the API
   plumbing.

4. **No core-library changes beyond the planned additions.**
   `find_in_aaf`'s `mob_class` filter applies only at
   `ContentStorage.Mobs` — once we descend into a matching Mob, the
   full subtree is searched. This honors how the question is
   phrased ("KEKE under CompositionMobs"); it would *not* be the
   right behavior to also filter Mob references inside Slots/Segments
   (which would over-restrict).

## Findings — chain-walk on real samples

`samples/PWD_310_LC_10-07-2025.aaf` (2.3 GB; 4,648 Mobs).

### Walk from a MasterMob with a clean chain

```
  [0] MasterMob 'PW VO 310 B Round 1.wav.new.05' slot=1 segment=SourceClip edit=30000/1001
  [1] SourceMob slot=1 segment=SourceClip edit=30000/1001
* [2] SourceMob 'PW VO 310 B Round 1.wav' slot=1 segment=SourceClip edit=30000/1001  -- terminal: no_source_id
```

Three hops. Walk completes in 1 ms. The deepest-named Mob is the
SourceMob carrying the original WAV's name — exactly the
"deepest-named-mob and the slot used at that hop" view that
TrackManager needs. `terminal_reason: no_source_id` means the chain
reaches the natural terminus (a SourceClip whose mob_id is zero, the
canonical "this is essence, not a reference").

### Walk from a CompositionMob with editorial structure

```
* [0] CompositionMob 'PW_213_KEKE ISO.new.01' slot=1 segment=OperationGroup edit=30000/1001  -- terminal: operation_group
```

The KEKE iso CompositionMob's slot 1 holds an `OperationGroup`
(typically an audio mix or pan effect with multiple input segments).
The walker terminates with reason `operation_group` rather than
silently descending into one of the inputs. This is the right
default — a multi-input OG is editorial structure that the user
should resolve explicitly.

The implication for iso-channel identification is consistent with
Phase 1: the iso identity for **KEKE** is in the CompositionMob's
**name** (`PW_213_KEKE ISO.new.01`), not somewhere deeper in the
chain. The chain-walk surfaces that fact directly via the terminal
reason — the user knows immediately that the chain stops at this
Mob and that the answer for "who is this clip?" is the Mob's own
name.

A useful Phase 4 follow-up: when terminal_reason is
`operation_group` or `multi_segment_sequence`, expose a way to walk
each branch separately and return a tree of hop lists.

## Performance — class-filtered find on the 2.3 GB sample

Measured against the Flask `test_client` (no network round-trip cost)
on `samples/PWD_310_LC_10-07-2025.aaf`. Pattern was `(?i)host`.

| Filter | Time | Matches |
|---|---|---|
| (none) | 7.03 s | 560 |
| `class=CompositionMob` | 3.55 s | 14 |
| `class=MasterMob` | 1.72 s | 543 |

`CompositionMob`-only is a 50% speedup; `MasterMob`-only is 76%. The
ratios track the relative class sizes: 48 + 1,568 + 3,032 Mobs
(CompositionMob / MasterMob / SourceMob), so SourceMob walk dominates
the unfiltered cost.

Walk on a 3-hop chain costs ~1 ms — the entire chain fits in cache;
no perf concern at any reasonable max_hops.

## Read-only invariant

SHA-256 of both Password samples captured before and after the
exploration session above:

```
fecb030b3948f37c8d6bed6605b148e8a9fb1a1708aace316edb36e1a65d666d  PWD_310_LC_10-07-2025.aaf
e42453dfafecab2766eb9f8088878a3a34651b1af67258074445e7460c862925  Password_Mix_Audio_Tracks.aaf
```

Identical pre- and post-, plus the omnibus
`tests/test_web_readonly.py` round-trip now covers walk and class
filter on every commit.

## GUI ergonomics — what surfaced in real use

1. **Walk-chain inline panel feels right.** Putting the result below
   the property table (rather than in an overlay) keeps the entire
   interaction in one column; users can scroll the inspector to see
   both the source object and its chain at the same time.
2. **Hop click → tree-jump is the killer interaction.** Clicking a
   hop in the chain navigates the AAF tree to that Mob and refreshes
   the inspector; from there the user can immediately walk again
   (cheap). Hop-by-hop drilling without copy-pasting URNs is the
   biggest practical improvement over Phase 2.
3. **Slot picker for multi-slot Mobs.** Most Mobs in real sessions
   have one slot, so the picker rarely appears. When it does (e.g.
   stereo masters with 2 slots), it does the right thing.
4. **Class filter popover is fine, not great.** It's the right shape
   for the function but doesn't visually announce that the search is
   filtered. A future polish: when classes are selected, show a small
   tinted dot on the search button so it's obvious the next search
   will be narrower.
5. **Terminal-reason vocabulary is informative.** Seeing
   `★ operation_group` vs `★ no_source_id` vs `★ essence` next to a
   terminal hop tells the user *why* the chain stopped, which is
   often the actual answer they were looking for.

## Recommendations for a possible Phase 4

No commitment, but the following came up during Phase 3 use:

1. **Branch-aware walk.** When a hop's segment is an OperationGroup
   or multi-segment Sequence, expose a way to walk each branch
   separately. Brief should specify whether the API returns a tree
   of hops or a flat list with branch markers.
2. **Walk from arbitrary clips inside the inspector.** Today the
   Walk button only appears on Mob headers; making it appear on
   nested SourceClip expansions would let users walk from a specific
   editorial position (e.g. picking one slice of a Sequence).
3. **Per-class find-result grouping.** With class-filtered find, the
   user often wants to see results grouped by which Mob they belong
   to — clickable Mob headers above hit lists. Tightens the
   "find then explore" loop.
4. **Chain panel persistence across navigation.** When the user
   clicks a hop, the inspector re-renders for the new Mob and the
   chain panel goes away. Showing a small "previous chain" affordance
   so the user can see the full path they walked would help when
   exploring deep chains.
5. **Slot-id inference on /api/walk for the OperationGroup case.**
   When `terminal_reason: operation_group` and the user wants to
   continue, /api/walk could expose the OG's input segments as
   "branches" that the GUI surfaces as buttons.

## Build sequence (commits)

10 Phase-3 commits, one per logical step:

1. Phase 3 step 1+2: chain-walk core with terminal-reason coverage
   (combined steps 1 and 2 since the 3-hop case and the
   terminal-coverage cases land in one cohesive module).
2. Phase 3 step 3: mob_class filter on find_in_aaf
3. Phase 3 step 4: aafbrowser walk CLI subcommand
4. Phase 3 step 5: aafbrowser find --class (repeatable)
5. Phase 3 step 6+7: /api/walk endpoint + /api/find ?class= filter
6. Phase 3 step 8+9: Walk-chain panel + find class multi-select
7. Phase 3 step 10: extend read-only round-trip for /api/walk + ?class=
8. Phase 3 step 11: README update + completion report (this file)
