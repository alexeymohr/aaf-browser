# AAF Browser — Phase 2 Brief

## Goal

Add a browser-based GUI on top of the existing `aafbrowser.core` library, so
that AAF files can be opened, browsed, searched, and inspected interactively
through expand/collapse trees, click-to-jump references, and a hex viewer for
CFB streams.

For project-level context read `docs/PROJECT_OVERVIEW.md` and the repo-root
`CLAUDE.md` first. Phase 1 (CLI + core) is complete and validated — see
`docs/phase1-completion-report.md`. This brief covers Phase 2 only.

## Why this and not more CLI

Phase 1 answered the original Password channel-identity question via
`aafbrowser find`. But the 2.3 GB session AAF has 4,648 Mobs, and any further
investigation that requires walking the graph by hand (chasing a
CompositionMob's slots → SourceClips → SourceMob references) is impractical
on the CLI. The whole shape of "what does this file actually look like" is
better browsed than queried.

## Scope

In:

- New `aafbrowser.web` package — a Flask app served locally
- A vanilla HTML/CSS/JS frontend, no build step
- A `aafbrowser web [<file.aaf>]` CLI subcommand that starts the server and
  optionally opens a file at launch
- One file open at a time (open / close / re-open)
- Hybrid loading: eager Mob index, lazy property serialization per object
- Two trees (AAF object graph + CFB storage tree) with expand/collapse
- Inspector pane with type-aware property rendering
- Click-to-jump on MobIDs and StrongRef/WeakRef targets
- Hex view for CFB streams with offset/length controls
- Search panel using `find_in_aaf` / `find_in_cfb` with deep-link results
- Class_id decoding for CFB storages (`0d010101-0101-0f00-...` → `SourceMob`)

Out:

- Multi-file tabs (one file at a time keeps the server state simple)
- AAF editing or any write operation — read-only invariant unchanged
- Diff between two AAFs
- Authentication / multi-user — local tool only, binds to 127.0.0.1
- Any frontend build pipeline, npm, or framework

## Architecture

### Backend (Flask)

```
aafbrowser/web/
├── __init__.py
├── app.py              # Flask app factory + routes
├── state.py            # Process-global open file + lock
└── static/             # Served statically — index.html, css, js
    ├── index.html
    ├── styles.css
    └── app.js
```

Process-global state because pyaaf2 is **not thread-safe** (verified: no
internal locks, all reads share one file handle through `seek` + `read`).
Two consequences:

1. Run Flask with `app.run(threaded=False)` OR put a single
   `threading.Lock()` around every `aaf2`/CFB access. Use the lock; it's
   safer if the dev later adds Werkzeug or a different runner.
2. State (current open file, its handle, the cached eager Mob index) is
   process-global — module-level in `state.py`, guarded by the lock.

### Frontend

Single-page app, three regions:

```
┌────────────────────────────────────────────────────────────────┐
│  Topbar: file path · open button · close button · find box    │
├──────────────────────────┬─────────────────────────────────────┤
│  Left pane:              │  Right pane: inspector              │
│   [AAF] [CFB] tabs       │   - breadcrumb path                 │
│   tree with twisties     │   - class + name + mob_id header   │
│   filter box             │   - properties table                │
│                          │   - reference badges (clickable)    │
├──────────────────────────┴─────────────────────────────────────┤
│  Find results panel (collapsible, bottom)                      │
└────────────────────────────────────────────────────────────────┘
```

Vanilla JS only — no React, no Vue, no build step. Use `<template>` elements
+ small render functions. This is a developer tool used occasionally; build
infrastructure would outweigh the win.

Visual style: dark utilitarian. Monospace for IDs, hex, class names, paths.
Sans-serif for human labels. No splashy design — function over form. Don't
clone any other project's brand styling.

## REST API

All endpoints return JSON. All responses include a `sha256` field of the
currently-open file when state-bearing, so the frontend can detect
inconsistency.

Endpoints (all under `/api`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/open` | Body `{path: str}` → open AAF file, build eager Mob index, return `{path, sha256, mob_count, classes_summary}` |
| `POST` | `/close` | Close current file |
| `GET` | `/file` | Current file metadata or 404 if none open |
| `GET` | `/mobs` | Eager Mob index. Returns `[{mob_id, class, name, slot_count}, ...]` for every Mob in `f.content`. Supports `?class=<...>&name_contains=<...>` filters server-side. |
| `GET` | `/object` | Single-object serialization. Query: `?mob_id=<urn>` OR `?path=<slash/path>`. Uses `core.serialize_object`. Lazy — called per click. |
| `GET` | `/cfb/tree` | CFB tree. Query: `?include_metadict=0\|1` (default 0). |
| `GET` | `/cfb/stream` | Hex bytes. Query: `?path=<cfb-path>&offset=<n>&length=<n>` (default offset=0, length=4096). Returns `{path, byte_size, offset, length, hex, ascii, truncated}`. |
| `GET` | `/find` | Search. Query: `?pattern=<regex>&in=<names\|values\|both>&layer=<aaf\|cfb\|both>`. Returns the same shape `core.find_in_*` already produces. |
| `GET` | `/resolve` | Reference resolution. Query: `?ref=<urn-or-auid-or-path>`. Returns the target's identifying triple `{kind, mob_id_or_path, class, name}` so the frontend can navigate to it. |

The endpoints are thin wrappers over the existing `core` functions. The
brief explicitly does NOT add new core functionality unless the GUI surfaces
a need that the existing core can't answer with a simple call.

### Errors

- File not open and endpoint requires it → `409 Conflict` with
  `{error: "no_file_open"}`
- Invalid `mob_id` or `path` → `404` with `{error: "not_found", detail: ...}`
- Bad regex → `400` with `{error: "bad_pattern", detail: ...}`
- Internal pyaaf2 error → `500` with `{error: "internal", detail: <msg>}`
  and a stderr trace; never expose stack to the browser

## Frontend behavior spec

### Open / close

- Topbar shows current path or "No file open"
- "Open" button reveals a path input (text field, not file picker — local
  tool, paths can be huge or symlinked) plus an "Open" submit
- "Close" button visible only when a file is open

### Left pane — AAF tab

- After `/open`, fetch `/mobs` and group by class. Each class is a
  collapsible section: `▶ CompositionMob (48)`, `▶ MasterMob (1568)`,
  `▶ SourceMob (3032)`, etc.
- Within a class, list rows: `<name-or-untitled>  <mob-id-tail-8>`
  - mob-id-tail-8 is the last 8 hex characters of the URN as a clickable
    badge; full URN on hover
  - "untitled" italic placeholder if name is empty
- Filter box at top of pane: substring match on name across all classes,
  shows only matching rows (collapse classes with zero matches)
- Click a row → fetch `/object?mob_id=<urn>` and render in inspector
- Selected row highlighted

### Left pane — CFB tab

- Tree of storages and streams. Storages have twisties; streams don't.
- Each row: `<icon> <name> [<class_id_decoded>] (<size>)` for storages,
  `<icon> <name> (<bytes>)` for streams
- Click a stream → inspector switches to hex view
- Click a storage → inspector shows its DirEntry properties (class_id,
  child count, path)
- Toggle: "Include MetaDictionary" checkbox (off by default per Phase 1
  precedent)

### Right pane — inspector

When showing an AAF object:

- Breadcrumb at top: `Mobs / SourceMob:KEKE / Slots / [3] / Segment` —
  each segment clickable, navigates back up
- Header: `<class>` (large), name (subdued), full mob_id (monospace,
  copy-on-click)
- Properties table:
  - Property name (left), type (badge), value (right)
  - Scalar values shown literally with `_type` badge
  - StrongRef → expand-in-place button: clicking expands the child object
    inline in the same inspector, with its own collapsible header
  - StrongRefVector / StrongRefSet → "[N items]" expand to show numbered
    child rows, each clickable
  - WeakRef → "→ <target-class>:<target-name>" badge that, when clicked,
    navigates the AAF tree to the target (or shows a small panel if it's
    a dictionary entry not surfaced in `/mobs`)
  - MobID values rendered as the same tail-8 badge style as in the Mob
    list, click navigates to that Mob
  - Bytes → "<N bytes>" with an "expand" affordance that lazily fetches
    full content (uses the same hex view)

When showing a CFB stream:

- Header: full CFB path, class_id (if any), byte_size
- Controls: `offset` and `length` numeric inputs, "Refresh" button
- Hex view: 16 bytes per row, offset gutter, hex middle, ASCII right
  (printable chars only, dots otherwise) — exactly like `xxd`

### Find panel

- Persistent input in topbar: pattern (regex)
- Toggles for `in` (names/values/both) and `layer` (aaf/cfb/both)
- "Search" button → `/find` → results render in collapsible bottom panel
- Each result row clickable: deep-links to the AAF tree (selects the Mob,
  expands ancestors, scrolls inspector to the matching property) or the
  CFB tree (selects the storage/stream and switches inspector to it)
- Search is synchronous on the server (Phase 1 finds run in seconds even
  on 2.3 GB); no progress UI needed in Phase 2

### Class_id decoding

The CFB tree's `class_id` AUIDs map to AAF class names via
`pyaaf2.metadict`. Decode them on the server in the `/cfb/tree` response:
`{class_id: "<urn>", class_name: "<MasterMob|...>"}`. If unknown, leave
`class_name: null`. Show whichever is non-null in the tree row, with the
other on hover.

## Dependencies

Add to `pyproject.toml`:

- `flask >= 3.0`

Pin to a specific version that satisfies the supply-chain rule (no version
released in the last 7 days). Flask 3.x is mature; pick the most recent
release that's at least 7 days old at build time.

No frontend dependencies — all JS/CSS is hand-written and committed.

## CLI integration

Add a `aafbrowser web` subcommand:

```
aafbrowser web [<file.aaf>] [--host HOST] [--port PORT] [--no-browser]
```

- Default host `127.0.0.1`, port `5173`
- If `<file.aaf>` provided, open it before serving so the page lands on
  the file already loaded
- On startup, print the URL and (unless `--no-browser`) open it via
  `webbrowser.open`
- Server runs in the foreground; Ctrl-C stops it cleanly and closes the
  AAF file

## Testing

- `tests/test_web.py`: use Flask's test client to exercise every endpoint
  against the same programmatically-built fixtures used in Phase 1. Cover:
  open / close, /mobs returns expected count, /object resolves by mob_id
  and by path, /cfb/tree default vs include_metadict, /cfb/stream returns
  hex with correct offset/length, /find returns structured matches,
  /resolve handles MobID / AUID / path, error cases (bad regex, unknown
  mob_id, no file open).
- Read-only invariant test: open via the API, exercise every endpoint,
  close — assert SHA-256 of the input file is unchanged.
- Frontend tests are out of scope; the JS is small enough to verify by
  hand and the API tests cover the data layer.

## Build sequence

Commit at each step.

1. `aafbrowser.web.state` — process-global state, lock, open/close.
2. `aafbrowser.web.app` — `/open`, `/close`, `/file`, with tests.
3. `/mobs` and `/object` endpoints, with tests.
4. `/cfb/tree`, `/cfb/stream`, with tests.
5. `/find`, `/resolve`, with tests.
6. CLI subcommand `aafbrowser web`, with smoke test.
7. `static/index.html` + `styles.css` skeleton (topbar + two panes + find
   panel), no JS yet — page renders but is empty.
8. `static/app.js` — open/close + Mob list rendering + class grouping +
   filter box.
9. AAF inspector with type-aware property rendering, breadcrumbs,
   inline expansion of StrongRefs and vectors.
10. Click-to-jump for MobIDs, WeakRefs, breadcrumb navigation.
11. CFB tab — tree rendering with class_id decoding, hex view for streams.
12. Find panel — query, results rendering, deep-link click handlers.
13. Read-only invariant test for the web layer.
14. README updates (web GUI install + usage), completion report.

## Acceptance criteria

1. `pip install -e .` followed by `aafbrowser web` opens a browser tab
   showing the empty UI.
2. `aafbrowser web samples/Password_Mix_Audio_Tracks.aaf` opens the file
   and the Mob list is populated within a few seconds.
3. The 2.3 GB sample (`samples/PWD_310_LC_10-07-2025.aaf`) opens; the
   eager Mob index returns 4,648 Mobs without serializing their
   properties (verify via timing — the open call should complete in
   reasonable time, single-digit-to-low-tens of seconds).
4. Clicking a Mob populates the inspector. Clicking a StrongRef expands
   it inline. Clicking a MobID-valued property (or a StrongRefVector
   item that points at another Mob) navigates the tree to that Mob.
5. CFB tab shows the storage tree with class_ids decoded to readable
   names where known. Clicking a stream shows a hex view; offset/length
   controls work.
6. Find panel: searching `(?i)channel|physicaltrack` against the small
   mix AAF returns 43 PhysicalTrackNumber matches (matches Phase 1
   findings); clicking a result jumps to the right Mob and scrolls to
   the property.
7. SHA-256 of every sample is unchanged after a full GUI session.
8. `pytest` exits 0 with new web-layer tests added.

## Concerns to flag rather than paper over

- If pyaaf2 in the target environment turns out to need additional
  serialization (e.g. on Mob index iteration mutating internal caches in
  a way that conflicts with serving multiple `/object` requests), stop
  and report. The verified behavior is single-handle reads with a global
  lock.
- If the 2.3 GB sample is genuinely too slow to load without progressive
  streaming, downgrade `/mobs` to paginated and report. Don't silently
  add streaming or async pyaaf2 access.
- If Flask 3.x has a recent (within 7-day window) point release at build
  time, pin to the most recent older release and note it.

## Completion report

When Phase 2 lands, produce `docs/phase2-completion-report.md` covering:

- What was built (matched against acceptance criteria).
- Any deviations with rationale.
- Performance notes from running against the 2.3 GB sample (open time,
  Mob list render time, /object click latency).
- Any GUI ergonomics that turned out worse than expected and would
  benefit from a follow-up.
- Recommendations for a possible Phase 3 (no commitment to one).
