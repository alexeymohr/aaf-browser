# AAF Browser — Phase 2 Completion Report

Date: 2026-04-27
Branch: main (14 Phase-2 commits, plus the 10 Phase-1 commits)
Test suite: **132 tests, all passing** (`pytest` exits 0, ~3.5 s).
Python: 3.14.3 / pyaaf2 1.7.1 / click 8.3.2 / flask 3.1.3 / pytest 9.0.3.

## What was built

Phase 2 adds a Flask + vanilla-JS web GUI on top of the existing
`aafbrowser.core` library. The Phase 1 CLI is unchanged; a new
`aafbrowser web` subcommand starts the GUI, optionally pre-opening an
AAF.

```
aafbrowser/web/
├── app.py        # Flask app factory; routes under /api
├── state.py      # Process-global open file + threading.Lock
└── static/
    ├── index.html
    ├── styles.css
    └── app.js
```

Endpoints (all under `/api`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/open` | Open AAF, build eager Mob index, return metadata |
| `POST` | `/close` | Close current file |
| `GET`  | `/file` | Current file metadata or 404 |
| `GET`  | `/mobs` | Mob index with `?class=` and `?name_contains=` filters |
| `GET`  | `/object` | Single-object serialization by `?mob_id=` or `?path=` |
| `GET`  | `/cfb/tree` | CFB tree with metadict class-name decoration |
| `GET`  | `/cfb/stream` | Lazy hex bytes from a single stream |
| `GET`  | `/find` | Regex search on AAF / CFB layers |
| `GET`  | `/resolve` | URN/AUID/path -> identifying triple |

All endpoints serialize pyaaf2 access through a single
`threading.Lock()` (`aafbrowser.web.state.state_lock()`). pyaaf2 is not
thread-safe; the lock is the only thing keeping concurrent requests
from corrupting the shared file handle.

### Acceptance-criteria checklist

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `pip install -e .` followed by `aafbrowser web` opens a browser tab showing the empty UI. | OK |
| 2 | `aafbrowser web samples/Password_Mix_Audio_Tracks.aaf` opens the file and the Mob list is populated within seconds. | OK (open 0.02 s, Mob list <1 ms) |
| 3 | The 2.3 GB sample opens and the eager index returns 4,648 Mobs without serializing properties. | OK (open 1.66 s, mob index immediate; see Performance below) |
| 4 | Clicking a Mob populates the inspector; StrongRefs expand inline; clicking a MobID-valued property navigates to that Mob. | OK |
| 5 | CFB tab shows the storage tree with class_ids decoded; clicking a stream shows a hex view; offset/length controls work. | OK |
| 6 | Find panel: searching `(?i)channel\|physicaltrack` against the small mix AAF returns 43 PhysicalTrackNumber matches; clicking a result jumps to the right Mob. | OK (43 matches verified in /find; click handler navigates and updates breadcrumb) |
| 7 | SHA-256 of every sample is unchanged after a full GUI session. | OK (verified pre/post against both samples; see below) |
| 8 | `pytest` exits 0 with new web-layer tests added. | OK (132 passed) |

### Test breakdown (Phase 2 additions)

- `tests/test_web_state.py` — 6 tests (lock, open/close, replace, concurrent serialization)
- `tests/test_web_api.py` — 39 tests (all 9 endpoints, filters, error envelopes, hex/ascii rows, class_name decoration, regex/scope/layer validation)
- `tests/test_web_cli.py` — 2 tests (help text + wsgiref smoke)
- `tests/test_web_readonly.py` — 1 omnibus test exercising every endpoint and asserting SHA-256 invariance throughout
- `tests/test_web_stub.py` (Phase 1) — removed; the stub no longer exists

Phase 1's 85 tests still pass unchanged.

## Deviations from the brief

1. **WSGI server is `wsgiref.simple_server`, not Flask's `app.run`.** Werkzeug 3.1.8 (Flask's bundled dev server) hangs at startup on Python 3.14.3 — `* Serving Flask app...` prints but the socket never binds. wsgiref's `make_server` is stdlib, single-threaded by default, and binds reliably. The brief permitted either `app.run(threaded=False)` or "a single threading.Lock around every aaf2/CFB access"; we use both — the lock covers correctness regardless of server choice. No functional difference for a single-user dev tool. Documented in the step-6 commit.
2. **No core-library changes.** Class_id → class-name decoding (`[MasterMob]`, `[ContentStorage]`, etc.) lives in `aafbrowser/web/app.py` as a post-processing pass over `core.cfb.cfb_tree`'s output. The brief explicitly forbids new core functionality unless the GUI surfaces a need that the existing core can't answer.
3. **Hex view `format_hex_ascii` helper is in `web/app.py`, not `core/cfb.py`.** Phase 1's `core.cfb.format_hex_view` returns formatted text rows (one per 16 bytes); the API contract wants split `hex[]` and `ascii[]` arrays. Same reasoning — keep `core` lean.

No semantic deviations from the brief's API or UI specifications.

## Performance — 2.3 GB sample (`samples/PWD_310_LC_10-07-2025.aaf`)

Measured against the Flask `test_client()` (no network round-trip cost):

| Endpoint | Time | Result |
|---|---|---|
| `POST /api/open` | **1.66 s** | 4,648 Mobs (3,032 SourceMob / 1,568 MasterMob / 48 CompositionMob) |
| `GET /api/mobs` | 4 ms | 4,648 entries returned |
| `GET /api/mobs?class=CompositionMob` | <1 ms | 48 entries |
| `GET /api/object?mob_id=...` (first SourceMob) | 4 ms | 14 properties serialized |
| `GET /api/object?mob_id=...` (CompositionMob `PW_213_KEKE ISO.new.01`) | 3 ms | full property table |
| `GET /api/cfb/tree` (default, no metadict) | **2.48 s** | full storage tree with class-name decoration |
| `GET /api/find` aaf layer, iso pattern | **4.94 s** | 6,415 matches |

Open + first Mob render is **~1.7 s**, well under the brief's "single-digit-to-low-tens of seconds" target.

The 2.5 s `/cfb/tree` cost is dominated by `f.cfb.walk()` traversing the full tree (including the MetaDictionary internally — we filter on output, not iteration). That's about as fast as it gets without changing pyaaf2.

`/find` at 4.94 s for the iso pattern across 4,648 Mobs is acceptable for the dev-tool target; the lock blocks other endpoints during this time, which is documented as fine for single-user usage.

Same-file confirmation on the small mix: open 0.02 s, /find iso pattern <1 ms, **43 matches** — matches Phase 1 exactly.

### Read-only invariant — real samples

SHA-256 hashes captured before and after running the full perf script
twice against both samples:

```
fecb030b3948f37c8d6bed6605b148e8a9fb1a1708aace316edb36e1a65d666d  PWD_310_LC_10-07-2025.aaf
e42453dfafecab2766eb9f8088878a3a34651b1af67258074445e7460c862925  Password_Mix_Audio_Tracks.aaf
```

Identical pre- and post-, in addition to the per-test SHA-256
round-trip in `tests/test_web_readonly.py`.

## Ergonomic findings (real-use observations)

1. **Mob list grouping by class works well at scale.** Auto-collapsing classes with >50 entries (in our case, MasterMob and SourceMob) keeps the left pane usable on the 2.3 GB session AAF; only CompositionMob (48 entries) is auto-open. The name filter is the right primary navigation tool.
2. **Tail-8 MobID badges are the right ergonomics.** Full URNs are unreadable in a list; the last 8 hex chars are enough to disambiguate within a single file and full URN is one hover away.
3. **Inline StrongRef expansion handles the common case.** Most navigation happens via Slots->Segment->Components, and inline expansion within one inspector view shows the chain without a deep breadcrumb stack. The breadcrumb only grows when the user explicitly clicks a MobID badge.
4. **Find -> click is the killer interaction for CFB.** Phase 1's CLI find prints paths; the GUI version expands ancestor storages and selects the entry, which is dramatically faster than copy/pasting paths into `--show-bytes`.
5. **What turned out worse than expected.**
   - Breadcrumb segments deeper than the first don't currently re-fetch — the trail label preserves the deeper path as muted text but clicking those segments doesn't navigate. Worth a follow-up: walk the path, push a fetcher per segment.
   - Clicking a StrongRefVector member doesn't push a breadcrumb segment; it expands inline. That's intentional but for very deep chains an "open in inspector" affordance per nested object would be useful.
   - The hex view shows up to 4 KB by default; for streams larger than that the user has to know to bump `length`. A "next 4 KB" pager would be a small win.
6. **Class_id decoding adds real value.** Seeing `[MasterMob]` next to a CFB storage `MasterMob-XXXX` is the kind of cross-layer mapping Phase 1 only gestured at; in the GUI it makes the CFB tab self-narrating.

## Recommendations for a possible Phase 3

No commitment, but if a third phase is ever briefed:

1. **Path-based breadcrumb that fetches per segment.** Today the breadcrumb model is a list of `{label, fetch}`; promote each segment of a deep AAF path into one of those, with `fetch` calling `/api/object?path=...` for the prefix. This makes any property path navigable, not just Mob roots.
2. **Pageable hex view.** Add prev/next buttons that increment/decrement offset by length. Cheap and obvious.
3. **A Mobs-by-CFB-storage view.** When a user clicks a CFB storage whose `class_name` resolves to a Mob class, offer "open the AAF object at this storage" — bridging the two layers in the same way that `--show-bytes` plus `inspect --mob-id` does in the CLI.
4. **Diff between two AAFs.** This is out-of-scope for Phase 2 and out-of-scope for the project's current motivation, but it would directly answer the original "is this metadata being dropped" question. Two `aafbrowser dump --json` outputs piped through a structural diff would be most of it.
5. **Multi-file tabs.** State.py is intentionally single-file to keep the lock simple. Multi-file would require either a per-file lock or copy-on-open into a new state slot. Worth doing only if real usage produces a clear case.
6. **Werkzeug fix.** When Werkzeug ships Python 3.14 support, swap wsgiref back out — werkzeug's reloader is nicer for development. Stdlib wsgiref is the right baseline for now.

## Build sequence (commits)

14 commits, one per step in the brief's build sequence:

1. Step 1: web state module + Flask app skeleton
2. Step 2: /open, /close, /file endpoints
3. Step 3: /mobs and /object endpoints
4. Step 4: /cfb/tree and /cfb/stream endpoints
5. Step 5: /find and /resolve endpoints
6. Step 6: `aafbrowser web` CLI subcommand
7. Step 7: HTML/CSS skeleton (no JS yet)
8. Step 8: Mob list JS — open/close, class grouping, name filter
9. Step 9: AAF inspector with type-aware property rendering
10. Step 10: click-to-jump for MobIDs and WeakRef detail
11. Step 11: CFB tab — tree rendering, class_id decoding, hex view
12. Step 12: find panel with deep-link click handlers
13. Step 13: read-only invariant test for the web layer
14. Step 14: README updates + completion report (this file)
