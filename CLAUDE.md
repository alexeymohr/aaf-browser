# CLAUDE.md — AAF Browser

Project rules and orientation for Claude Code sessions working in this repo.

## What this is

A read-only inspection tool for AAF (Advanced Authoring Format) files. AAF is
the container format Avid Pro Tools and Media Composer use to exchange editorial
sessions. AAF files are built on Microsoft Structured Storage (MS-CFB) and
contain a graph of typed objects.

This tool exposes both layers — the raw CFB storage/stream tree and the AAF
object graph (Mobs, Slots, Components, Descriptors, etc.) — to support
debugging questions like "is this metadata actually present in the file or am I
being lied to by some intermediate tool?"

Primary motivating use case: determine whether physical recorder channel
identity is recoverable from Password show iso AAFs. See
`docs/PROJECT_OVERVIEW.md` for full context.

## Non-negotiable invariants

1. **Read-only.** Never modify input AAF files. Always open with `aaf2.open(path, 'r')`.
   Tests must verify file hashes are unchanged after any operation. If a future
   feature seems to require writing, stop and flag it — the answer is no.

2. **No data loss in serialization.** When converting AAF property values to
   JSON, preserve type information via a `_type` discriminator field. Never
   silently coerce an AAFRational to a float, an AUID to a generic string
   without marking it, or a datetime to an ISO string without flagging the
   type. Round-trip fidelity matters.

3. **Cycle-safe traversal.** AAF object graphs can cycle (especially through
   the dictionary). Every recursive walker must track visited object identities
   (`id(obj)`) and emit a cycle marker rather than recursing.

4. **Lazy stream reads.** Some CFB streams hold large binary payloads (essence
   data, indexes). Never load full stream bytes eagerly. Expose byte-range
   reads via `DirEntry.open('r').seek(offset); read(length)`.

5. **Core stays pure.** `aafbrowser.core` knows nothing about CLI argument
   parsing, terminal formatting, Flask, or HTTP. The CLI and (future) web
   layers consume the core; the core never imports from them.

## Architecture

```
aafbrowser/
├── core/           # Library — used by both CLI and web
│   ├── cfb.py      # Raw CFB walker (uses pyaaf2's f.cfb, NOT olefile)
│   ├── aaf.py      # AAF object graph walker (pyaaf2)
│   ├── serialize.py # Property values → JSON-friendly form with type tags
│   └── resolver.py # MobID and reference lookup
├── cli/            # Click-based CLI consuming core
│   └── __main__.py # Console script entry point: `aafbrowser`
└── web/            # Phase 2 — empty stub for now
    └── app.py
```

Phase 1 ships `core` + `cli`. Phase 2 (separate brief, not yet written) adds
the web GUI.

## Dependencies and why

- **pyaaf2** — AAF object model. Already in use in adjacent projects
  (FirstPass / pt-mix-tool). Exposes `f.cfb` as well, so we don't need olefile.
- **click** — CLI framework. Composable, widely understood.
- **pytest** — tests.

We deliberately do NOT depend on `olefile`. pyaaf2's `CompoundFileBinary` reads
the same MS-CFB format using the already-open file handle. Adding olefile means
opening the file twice with two parsers, which complicates read-only guarantees
and adds a dependency for no functional gain.

## Property type handling

`obj.properties()` yields `Property` instances. Their concrete types determine
how to serialize their values:

| Property class | Value type | Serialization |
|---|---|---|
| `Property` | scalar (str, int, bool, datetime, AAFRational, AUID, MobID) | tag with `_type`, emit primitive form |
| `StrongRefProperty` | child `AAFObject` | recurse, emit nested object |
| `StrongRefVectorProperty` | ordered list of `AAFObject` | recurse each, emit array |
| `StrongRefSetProperty` | unordered set of `AAFObject` | recurse each, emit array (preserve iteration order) |
| `WeakRefProperty` | reference to a dictionary entry | emit reference + resolved target's identifying info, do NOT recurse into the dictionary |

Special scalar types:
- `AAFRational` → `{"_type": "rational", "num": N, "den": D, "value": N/D}`
- `MobID` / `AUID` / `UMID` → URN string with `_type` tag (pyaaf2 already
  formats these as URNs, e.g. `urn:smpte:umid:...`)
- `datetime` → ISO 8601 string with `_type: "datetime"`
- `bytes` → base64 with `_type: "bytes"` and a `length` field

## CFB layer notes

`f.cfb.walk()` yields `(path: str, storages: List[DirEntry], streams: List[DirEntry])`.

`DirEntry` exposes:
- `name`, `byte_size`, `class_id` (AUID)
- `path()` (method, returns `/path/to/entry`)
- `isfile()`, `isdir()`, `isroot()`
- `open('r')` returns a `Stream` supporting `seek` + `read`

The CFB tree is much larger than people expect. Even an empty AAF contains
thousands of streams from the MetaDictionary, ClassDefinitions, and
TypeDefinitions. The default `cfb` command output MUST filter the
MetaDictionary subtree by default (use `--include-metadict` to opt in) and
should also collapse repetitive sibling sequences when count > some threshold.
A raw unfiltered dump is unusable for humans.

Storages have meaningful `class_id` values (e.g. ContentStorage, Header,
specific Mob classes) — surface these in CFB output so users can map storage
locations to AAF concepts.

## Testing

- Unit tests live in `tests/` mirroring the package layout.
- Fixtures in `tests/fixtures/` are small AAFs created programmatically by
  test setup (so we never check binary AAFs into the repo).
- Real-world AAFs live in `samples/` (gitignored).
- Read-only invariant test: hash the input file before and after every CLI
  command and assert equality.

## Sample AAFs

Drop real AAFs (Password iso AAFs, etc.) into `samples/`. This directory is
gitignored. Tests against samples are opt-in (e.g. via a `--with-samples`
pytest flag) so CI can run without them.

## Documentation

- This file (`CLAUDE.md`) — project rules and orientation. Update when
  invariants or architecture change.
- `docs/PROJECT_OVERVIEW.md` — evergreen high-level overview and phase
  roadmap. Update when phases land or scope shifts.
- `docs/phase1-brief.md` — Phase 1 build brief. Frozen once Phase 1 lands;
  later phases get their own brief files.
- `README.md` — installation + usage examples for end users.

## When in doubt

- Output? Tag every value with `_type`. JSON consumers can ignore tags;
  losing them later is harder than ignoring them now.
- Recursion? Track `id(obj)` and emit cycle markers.
- Performance? Lazy. Don't read what hasn't been asked for.
- Surface area? Keep `core` pure. CLI and web are thin adapters.
- Modification? Don't. Read-only is non-negotiable.
