# AAF Browser — Phase 1 Completion Report

Date: 2026-04-27
Branch: main (10 commits, one per build step)
Test suite: **85 tests, all passing** (`pytest` exits 0).
Python: 3.14.3 / pyaaf2 1.7.1 / click 8.3.2 / pytest 9.0.3.

## What was built

Phase 1 ships exactly what `docs/phase1-brief.md` asked for: the
`aafbrowser` package with a pure `core` library, a Click CLI registered as
the `aafbrowser` console script, and a `web` skeleton stub.

```
aafbrowser/
├── core/
│   ├── serialize.py    # Type-tagged JSON projection of property values
│   ├── aaf.py          # AAFObject graph walker (cycle-safe, JSON + human)
│   ├── cfb.py          # CFB walker (MetaDictionary filtered by default)
│   └── resolver.py     # MobID + path resolution + regex search
├── cli/__main__.py     # Click commands: tree / dump / cfb / inspect / find
└── web/app.py          # Stub: raises NotImplementedError("Phase 2")
```

### Acceptance-criteria checklist

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `pip install -e .` registers the `aafbrowser` console script. | OK |
| 2 | All five CLI commands run on a fixture AAF and exit 0. | OK (CLI tests) |
| 3 | `aafbrowser dump fixture.aaf --json \| python -m json.tool` succeeds — output is valid JSON. | OK (1,990 lines validated) |
| 4 | `aafbrowser cfb` excludes MetaDictionary by default; `--include-metadict` includes it. | OK (CFB tests) |
| 5 | `aafbrowser cfb --show-bytes <stream>` produces a 4 KB hex view by default. | OK (CLI test) |
| 6 | `aafbrowser inspect --mob-id <id>` resolves and shows the mob's properties. | OK (CLI test) |
| 7 | `aafbrowser find --pattern <known-present>` returns at least one match with path + classname. | OK (CLI test + real samples) |
| 8 | Cycle test emits a marker, never infinite-loops. | OK (test_aaf cycle tests) |
| 9 | SHA-256 of the input fixture is identical before and after every command. | OK (test_readonly + verified on real samples) |
| 10 | Type-tag round-trip: AAFRational and datetime emit `_type` envelopes. | OK (test_serialize) |
| 11 | `pytest` exits 0. | OK (85 passed in 2.2 s) |

### Test breakdown

- `tests/test_serialize.py` — 29 tests
- `tests/test_aaf.py` — 7 tests
- `tests/test_cfb.py` — 15 tests
- `tests/test_resolver.py` — 13 tests
- `tests/test_cli.py` — 18 tests
- `tests/test_readonly.py` — 2 tests (exercises 19 CLI invocations against
  a per-test-copied fixture and asserts SHA-256 equality after each)
- `tests/test_web_stub.py` — 1 test

## Deviations from the brief

Two minor deviations, neither of which changes observable behavior in a way
a downstream consumer should care about:

1. **AUID stringification.** The brief's serialization table parenthetically
   notes "URN string from pyaaf2" for AUID/MobID/UMID. In pyaaf2 1.7.1 this
   is true for `MobID` (stringifies as `urn:smpte:umid:...`) but NOT for
   `AUID` — the latter stringifies as a hyphenated UUID like
   `01030202-0200-0000-060e-2b3404010101`. We honor the spec's discriminator
   shape (`{"_type": "auid", "value": "<str>"}`) and emit whatever
   `str(auid)` produces. The `_type` tag preserves identity regardless of
   surface form, so consumers can disambiguate without ambiguity.

2. **Unknown scalar fallback.** The brief allows it explicitly. We emit
   `{"_type": "unknown", "python_type": ..., "repr": ...}` for any value
   whose Python type isn't covered by the table, plus a one-line stderr
   warning. Nothing is silently dropped.

Click had a release inside the supply-chain 7-day window
(`8.3.3` published 2026-04-22, 5 days before today); pinned to
`click==8.3.2` (published 2026-04-03, 24 days old) per the rule. pyaaf2
1.7.1 is from 2023 — outside the window. pytest 9.0.3 is 20 days old.

## Findings — `aafbrowser find` against real samples

`samples/` contains three Password-show AAFs. We ran the validation
pattern from the brief
(`(?i)channel|chan_?id|physicaltrack|isolat|cam(era)?_?\d|mic_?\d`)
against the two we could load (the third is 20 GB and we did not attempt
it). SHA-256 of every input file is unchanged after every read.

### `samples/Password_Mix_Audio_Tracks.aaf` (325 KB — finished mix)

- 1 `CompositionMob` named `Password_Mix_Audio_Tracks`
- 44 `TimelineMobSlot`s, each with both a meaningful `slot.name`
  (`'GUIDE'`, `'VO 1'`, `'VO 2'`, `'Host A'`, `'Host B'`, …) and a
  `PhysicalTrackNumber` (1, 2, 3, …)
- The `find` pattern returned **43 matches**, all on the
  `PhysicalTrackNumber` property name (one per slot 0..42; slot 43 is
  the timecode slot which lacks the property).

In a finished mix like this one, channel identity is fully recoverable at
the AAF object layer: `slot.name` gives the human label and
`PhysicalTrackNumber` gives the source-track index.

### `samples/PWD_310_LC_10-07-2025.aaf` (2.3 GB — full session)

- **4,648 Mobs**: 3,032 `SourceMob`, 1,568 `MasterMob`, 48
  `CompositionMob`
- The `find` pattern returns matches across most Mobs' Slot/PhysicalTrackNumber.
- `SourceMob.name` carries the camera/recorder file name
  (e.g. `EPWD0209_CWZ09_178FF_HD_TXM_EN-US`), with up to 14 timeline
  slots per source — `PhysicalTrackNumber` is the only thing
  distinguishing slots within the same source (slot.name is empty
  there).
- `MasterMob.name` carries individual asset names like
  `PW VO 310 B Round 1.wav.new.05`.
- A `CompositionMob` named `PW_213_KEKE ISO.new.01` was observed: the
  iso-recorder identity for the contestant **KEKE** is in the
  `CompositionMob.name`, not in any slot.name. Slot names within the
  iso CompositionMob are empty.
- The CFB-layer scan with the same regex returned **no matches** — the
  raw storage tree does not surface channel identity in storage names
  or class IDs, contradicting the earlier hypothesis (referenced in
  `docs/PROJECT_OVERVIEW.md`) that the metadata lives in a "sub-folder
  structure". It lives in the AAF object graph, specifically in
  `CompositionMob.name` + `slot.PhysicalTrackNumber` pairs.

**Validation conclusion for the original Password question:** the metadata
needed to distinguish iso channels at the editorial level is present and
recoverable from the AAF. The path is:

1. The CompositionMob name encodes the iso identity (e.g. `PW_213_KEKE
   ISO.new.01` → contestant KEKE).
2. Within a SourceMob, individual physical tracks are distinguished by
   `slot.PhysicalTrackNumber`, not by `slot.name`.
3. The CFB layer adds nothing new for this question — the answer is in
   the object graph.

This is exactly the answer that motivated the tool: the prior claim of a
"sub-folder structure" was misleading; the truth is in the AAF object
layer.

### `samples/PW_310_AUDIO_MAP_A AND B GAMES.aaf` (20 GB)

Not attempted — the file is too large for an opportunistic exploratory
run. The same pattern would work; pyaaf2 has to parse the dictionary
upfront, which on a 20 GB AAF would take meaningful time. Worth
attempting in a focused session if/when it becomes interesting.

## Recommendations for the Phase 2 (web GUI) brief

A few things became visible during CLI use that the GUI should pick up:

1. **Two-pane navigation, AAF-on-left + reference-on-right.** With 4,648
   Mobs in a real session AAF, scrolling a giant `dump` is brutal; the
   CLI works for grep-style queries but not for browsing. The web GUI
   should let you click a Mob in a list, see its properties, click a
   StrongRefVector to expand it, click a WeakRef to jump to the target
   in the dictionary in the right pane.
2. **Sort/filter on Mob lists.** Group by class (Composition / Master /
   Source) and filter by name. The CLI currently shows everything in
   walk order; in a GUI you want to bin-and-sort.
3. **A "find" results panel with deep links.** `find` already returns
   `{layer, path, classname, field, value, where}`; the GUI should
   render those as clickable rows that jump to the AAF or CFB tree
   pre-expanded.
4. **CFB vs AAF cross-mapping.** The brief pointed out that storages
   have meaningful `class_id` AUIDs that map to AAF classes
   (ContentStorage / Header / specific Mob classes). The CLI surfaces
   raw class_ids; the GUI should render a class_id as `MasterMob` etc.
   when known. `pyaaf2.metadict` has the mapping.
5. **Lazy loading on giant sessions.** The 2.3 GB AAF parses fine but
   serializing all 4,648 Mobs into one JSON object is unwieldy. The GUI
   should fetch one Mob at a time on demand (the core's
   `serialize_object` already takes a single object — the API is
   already shaped for this).
6. **A path-bar with breadcrumbs.** `Mobs/<urn>/Slots/0/Segment` is
   already what `inspect --path` uses; in the GUI that becomes a
   browser-style breadcrumb.
7. **MobID URN ergonomics.** In a 4,648-Mob AAF, copy/pasting full URN
   strings is tedious. The GUI should show the last 8 hex chars as a
   clickable badge with the full URN on hover.

The core library surface is in good shape for any of this — the
`serialize_object`, `cfb_tree`, `find_in_aaf`, and `find_in_cfb`
functions all return JSON-friendly structures that a Flask handler can
hand directly to a frontend.

## Build sequence (commits)

10 commits, one per step in the brief's build sequence:

1. Step 1: scaffold project
2. Step 2: type-tagging serializer
3. Step 3: AAF object graph walker with cycle detection
4. Step 4: CFB walker with MetaDictionary filtering
5. Step 5: resolver and search
6. Step 6: CLI commands
7. Step 7: web stub test
8. Step 8: read-only invariant tests
9. Step 9: README usage examples
10. Step 10: completion report (this file)
