# AAF Browser — Phase 3 Brief

## Goal

Add two operational features that turn AAF Browser from a *structural*
inspector into an *answer engine* for the questions that come up
repeatedly during AAF investigations:

1. **Chain-walk.** Given any starting reference (a Mob, a SourceClip, a
   `Mobs/<id>/Slots/<n>` path), follow the SourceID/SourceMobSlotID
   chain hop-by-hop until it terminates, returning the per-hop tuple
   `(mob_class, mob_name, mob_id, slot_id, slot_name, segment_class,
   physical_track_number, edit_rate, terminal, terminal_reason)`.
2. **Mob-class filter on `find`.** Restrict the AAF-layer search to
   Mobs of a given class. Faster on big sessions and a closer match for
   how the question is usually phrased ("KEKE under CompositionMobs").

For project-level context read `docs/PROJECT_OVERVIEW.md` and the
repo-root `CLAUDE.md`. Phase 1 (CLI + core) and Phase 2 (web GUI) are
complete — see `docs/completion_reports/`. This brief covers Phase 3 only.

## Why these two

Phase 1 + Phase 2 surface the AAF graph but leave the multi-hop walk
(SourceClip → MasterMob → SourceMob → EssenceData) to manual chasing.
Every iso-channel question and every "which essence file does this
clip come from" question is the same chain. A primitive that returns
the hop list once collapses dozens of `inspect`/click steps into one
call, and the *deepest-named-mob* answer (which is what TrackManager
needs for iso channel identification) falls out as a one-liner over the
hop list.

The class filter is a smaller, ergonomic win. PWD_310's 2.3 GB session
has 4,648 Mobs across 3 classes; restricting the walk to one class
matches the common framing and shaves the search by ~60-70% in
practice.

## Scope

In:

- `aafbrowser.core.chain` — pure walker, returns a `Hop` list.
- `find_in_aaf(...)` gains an optional `mob_class` filter parameter.
- CLI: new `aafbrowser walk` subcommand; `aafbrowser find` gains
  `--class <name>` (repeatable).
- Web: new `GET /api/walk` endpoint; `/api/find` accepts `?class=`.
- Web GUI: a "Walk chain" affordance in the AAF inspector when the
  current object has a chainable reference; renders the hop list as a
  clickable sequence in the right pane (or a small overlay).
- Tests for everything, including a synthetic chain fixture and
  programmatic cycle.

Out:

- Multi-channel decomposition / channel mapping per slot. The hop list
  carries enough information to compute that downstream; this brief
  doesn't add the higher-level synthesis.
- Editing or any write path — read-only stays non-negotiable.
- Visualizing the hop list as a graph (SVG/canvas). The list view is
  the right v1; a graph would be a Phase 4 question.
- Server-side caching of chain results. Walks are bounded by the chain
  depth (typically ≤ 6 hops) and cheap; caching adds invalidation
  complexity for no measured win.

## Verified API surface (use this, don't guess)

These have been confirmed against pyaaf2 1.7.1 in earlier phases.
Implement against them directly; if behavior differs in a future
pyaaf2, stop and report.

### SourceClip → reference

```python
# A SourceClip (a Component class) exposes:
clip.source_id           # MobID (zero if no upstream)
clip.source_mob_slot_id  # int — slot to enter in the upstream Mob
clip.start_time          # int — sample offset within the upstream slot
clip.length              # int — duration in slot edit-rate units
```

The mob set used for follow lookups is `f.content.mobs.get(mob_id)`,
which accepts a `MobID` instance. Use the existing
`resolver._try_parse_mob_id` to be input-tolerant.

### Mob slots

`mob.slots` is iterable; each slot has `.slot_id`, `.name` (often
empty), `.segment` (Component), and on TimelineMobSlot may carry
`.edit_rate` and `.PhysicalTrackNumber`. Keep iterating — `slot_id`
is **not** the same as the iteration index, so look up by `slot_id`
explicitly.

### Segment shapes that need handling

A slot's `segment` can be any Component subclass. Phase 3 chain-walk
must handle:

- **`SourceClip`** — primary chain edge. Has `source_id` +
  `source_mob_slot_id`; if `source_id` is the zero MobID, terminal.
- **`Sequence`** — a list of child Components. The walker's policy:
  if the sequence has a single non-Filler child, descend into it; if
  multiple non-Filler children exist, terminate with reason
  `multi_segment_sequence` and surface the sequence as the hop's
  segment_class (the caller can pick a child by index in a follow-up
  call).
- **`Filler`** — silence/blank; terminal with reason `filler`.
- **`Timecode`** — terminal with reason `timecode`.
- **`EssenceGroup`** — terminal with reason `essence_group`; record
  the count of choices but don't pick one (out-of-scope).
- **`OperationGroup`** — common in CompositionMob slots (e.g. fades,
  pans). Treat as a sequence: if exactly one input segment, descend;
  otherwise terminal with reason `operation_group`.
- Anything else: terminal with reason `non_clip_segment` and
  `segment_class` set to the actual class name.

If the SourceClip's referenced Mob isn't in `f.content.mobs`,
terminate with `broken_ref`. If we re-enter a previously-visited
`(mob_id, slot_id)` pair, terminate with `cycle`.

## Hop schema

```python
@dataclass(frozen=True)
class Hop:
    mob_class: str                    # "CompositionMob" | "MasterMob" | "SourceMob" | ...
    mob_name: Optional[str]
    mob_id: str                       # URN
    slot_id: Optional[int]
    slot_name: Optional[str]
    segment_class: str                # "SourceClip" | "Sequence" | "Filler" | ...
    physical_track_number: Optional[int]
    edit_rate: Optional[str]          # "num/den" string when present
    start_time: Optional[int]         # sample offset in upstream slot
    length: Optional[int]             # duration in slot edit-rate units
    terminal: bool
    terminal_reason: Optional[str]    # see segment-shape table; None on non-terminal hops
```

`Hop` is a dataclass so JSON serialization is trivial via `asdict`.
`mob_id` is always a URN; `edit_rate` is the same string form Phase 1
emits for AAFRational scalars (`"30000/1001"` etc.).

The walk returns a `list[Hop]` ordered start → terminal. The terminal
hop has `terminal=True` and a non-null `terminal_reason`. An empty
list is never returned — at minimum, the start hop is included even
if the start itself is terminal.

## Core API

`aafbrowser/core/chain.py`:

```python
def walk_chain(
    handle: AAFFile,
    start: Union[AAFObject, MobID, str],
    *,
    slot_id: Optional[int] = None,
    max_hops: int = 64,
) -> list[Hop]:
    ...
```

- `start` accepts an `AAFObject` (Mob or SourceClip), a `MobID`, or a
  URN/dotted-hex/plain-hex string (re-uses the resolver's parser).
- `slot_id` selects the entry slot when the start resolves to a Mob.
  If omitted, defaults to the slot at index 0 (matching the find-result
  ergonomics).
- `max_hops` is a safety bound. Hitting it terminates with reason
  `max_hops_reached`. The default of 64 is generous; real chains in
  Password material max out at ~5.

`find_in_aaf` gains a kwarg:

```python
def find_in_aaf(
    handle, pattern, *,
    in_names=True, in_values=True,
    mob_class: Optional[str] = None,
) -> Iterator[Match]:
    ...
```

When set, the walker skips any Mob whose `type(mob).__name__` doesn't
match. Implementation: filter at the top of the walk (the Mobs
collection); deeper traversal is unchanged.

## CLI

### `aafbrowser walk <file.aaf>`

```
aafbrowser walk <file.aaf>
    (--mob-id <id> [--slot <n>] | --path <slash/path>)
    [--json]
    [--max-hops N]
```

Default human output: one line per hop, indented to depth, showing
`<mob_class> "<mob_name>" slot=<slot_id> segment=<segment_class>
ptn=<n>` plus the terminal marker on the final hop.

`--json` emits `{"file": ..., "sha256": ..., "start": ..., "hops":
[...]}` where `start` echoes the resolved entry point.

### `aafbrowser find` gains `--class <name>`

```
aafbrowser find <file.aaf> --pattern <regex> [--class <name>]...
```

Repeatable: `--class CompositionMob --class MasterMob` searches both.
A single class is the common case; repeatability covers the "all but
SourceMob" framing without having to invert.

## REST API

### `GET /api/walk`

Query parameters (one of `mob_id` / `path` is required):

- `mob_id=<urn-or-hex>` — Mob to enter. `slot_id=<n>` optional.
- `path=<slash/path>` — slash-separated path; resolves via
  `resolver.resolve_path` and passes the result as the start.
- `max_hops=<n>` — defaults to 64.

Response:

```json
{
  "sha256": "<hex>",
  "start": { "mob_id": "<urn>", "slot_id": 0, "class": "CompositionMob", "name": "..." },
  "hops": [ { /* Hop dict */ }, ... ]
}
```

Errors follow the Phase 2 contract: 409 no_file_open, 404 not_found
(unresolvable start), 400 bad_request (missing args), 500 internal.

### `GET /api/find` gains `?class=<name>` (repeatable)

`?class=CompositionMob&class=MasterMob` (Flask reads as a list via
`request.args.getlist("class")`). Empty/missing means no filter
(current behavior).

## Web GUI

### Inspector "Walk chain" affordance

Whenever the inspector is showing an AAF object that has a chainable
reference, render a "Walk chain" button next to the object header.
Detection: object's class is `SourceClip` (uses `source_id`), or the
object is a Mob (chain starts via `slot_id` selection — show a small
slot picker drop-down next to the button if the Mob has more than one
slot).

Clicking the button calls `/api/walk` and renders the hop list in a
new sub-region of the inspector below the property table:

```
┌─ Chain ─────────────────────────────────────────────────────┐
│ ◉ CompositionMob "PW_213_KEKE ISO.new.01"  slot=1           │
│   segment=SourceClip  edit=30000/1001  ptn=1                │
│ ◉ MasterMob "..."  slot=1                                    │
│   segment=Sequence  → 1 child                                │
│ ● SourceMob "EPWD0209_..."  slot=3                          │
│   segment=SourceClip  ptn=3  ★ terminal: essence            │
└──────────────────────────────────────────────────────────────┘
```

Each hop is clickable: clicking a hop navigates the AAF tree to that
hop's Mob (using the same `navigateToMobId` from Phase 2, plus a
breadcrumb segment for the slot).

Terminal hops are visually distinct (filled marker, `terminal_reason`
shown).

### Find panel: class filter

Add a `<select multiple>` with options populated from the eager Mob
index's `classes_summary`. Default empty (= no filter). The selected
classes are passed to `/api/find` as `class` repeated query params.
The mob list filter on the left pane already filters by class
implicitly (each class section); the find filter is independent.

### Visual style

Match the Phase 2 dark utilitarian palette. Chain hops use the same
monospace + accent colors; terminal markers use the warning color
already defined for WeakRefs.

## Dependencies

No new dependencies. Core chain walker uses existing pyaaf2; the GUI
uses the existing vanilla-JS layer.

## Testing

- `tests/test_chain.py`:
  - Programmatic fixture with a 3-hop chain
    (CompositionMob → MasterMob → SourceMob, terminal essence).
  - Cycle case: two SourceMobs that reference each other; assert
    `terminal_reason == "cycle"`.
  - Broken-ref case: SourceClip whose `source_id` is a MobID not in
    the file; assert `terminal_reason == "broken_ref"`.
  - Filler / Timecode / multi-segment Sequence terminals.
  - `max_hops` enforcement.
- `tests/test_resolver.py` extensions: `find_in_aaf(..., mob_class=...)`
  with positive and negative matches.
- `tests/test_cli.py`: `aafbrowser walk` and `aafbrowser find --class`
  invocations.
- `tests/test_web_api.py`: `/api/walk` happy path and error envelopes;
  `/api/find?class=` filter.
- `tests/test_web_readonly.py`: extend the round-trip to include the
  new endpoints. SHA-256 of the input file must remain unchanged.

## Build sequence

Commit at each step.

1. `aafbrowser.core.chain` — `Hop` dataclass, `walk_chain` skeleton
   handling the simple SourceClip → SourceID case. Tests for the
   3-hop fixture chain.
2. Extend `walk_chain` with terminal-reason coverage:
   Filler / Timecode / multi-segment Sequence / OperationGroup /
   broken-ref / cycle / max_hops. Add the relevant tests.
3. `find_in_aaf(..., mob_class=...)` plus tests.
4. CLI `aafbrowser walk` subcommand with human + `--json` output;
   tests via `CliRunner`.
5. CLI `aafbrowser find --class` flag (repeatable) plus tests.
6. `GET /api/walk` endpoint (lock-protected) plus tests.
7. `GET /api/find?class=` plumbing plus tests.
8. Frontend: chain panel rendering in the inspector, slot picker for
   Mob starts, hop clickability.
9. Frontend: find class filter `<select multiple>` populated from the
   `classes_summary`.
10. Read-only round-trip extension covering the new endpoints.
11. README + completion report
    (`docs/completion_reports/phase3-completion-report.md`).

## Acceptance criteria

1. `aafbrowser walk samples/PWD_310_LC_10-07-2025.aaf --mob-id <KEKE_comp_mob_urn>`
   prints a hop list ending at a SourceMob whose `name` carries the
   physical capture identity; the deepest-named-mob row identifies the
   iso channel (verified by inspecting the trail).
2. `aafbrowser walk fixture --json | python -m json.tool` is valid
   JSON; the `hops` array is non-empty; the final hop has
   `terminal: true` and a non-null `terminal_reason`.
3. `aafbrowser find samples/PWD_310_LC_10-07-2025.aaf --pattern KEKE
   --class CompositionMob` returns matches only from CompositionMob
   subtrees; running time is materially shorter than the unfiltered
   case (target: ≥ 50% reduction on PWD_310).
4. A synthetic cyclic fixture causes `walk_chain` to terminate with
   `terminal_reason: "cycle"` and never infinite-loops.
5. The web GUI shows a "Walk chain" button on a SourceClip / Mob; one
   click renders the hop list; clicking a hop navigates to that Mob.
6. Find panel honors the class multi-select: searching with
   `CompositionMob` selected returns zero matches for a query that
   only hits SourceMob property values.
7. SHA-256 of every sample file is unchanged after a session that
   exercises walk + class-filtered find via both CLI and web.
8. `pytest` exits 0 with new tests added.

## Concerns to flag rather than paper over

- If pyaaf2's SourceClip API differs (e.g. `.source_id` not present on
  the version installed), stop and report. Don't synthesize references
  by reading raw properties without the verified attribute.
- If a Sequence's "single non-Filler child" rule turns out to skip
  meaningful editorial structure on real AAFs (e.g. the iso clip has
  multiple SourceClips inside a Sequence representing splits), revise
  the rule before shipping. The completion report should show the
  hop output for at least one Password CompositionMob to confirm.
- Class-filter `find` interacts subtly with WeakRef target-name
  matching: WeakRef targets live in the Dictionary, not in any Mob,
  so they're unaffected by the filter. Document this.
- If `walk_chain` cycle detection produces false-positives on legal
  AAFs (e.g. a Mob legitimately referenced from two different parent
  slots), the `(mob_id, slot_id)` pair as the visited key may need to
  become `(mob_id, slot_id, position-in-segment)`. Flag and revise.

## Completion report

When Phase 3 lands, produce
`docs/completion_reports/phase3-completion-report.md` covering:

- Acceptance-criteria checklist.
- Any deviations with rationale.
- Hop-list output for at least one Password CompositionMob (e.g. the
  KEKE iso) showing the chain that resolves the channel question end
  to end.
- Performance notes: chain-walk latency on real samples, class-filter
  speedup on the 2.3 GB AAF.
- GUI ergonomics — what the chain panel revealed in real use, what's
  worth refining in a follow-up.
- Any candidate Phase 4 ideas surfaced by Phase 3 use (no
  commitment).
