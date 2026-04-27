# AAF Browser — Phase 1 Brief

## Goal

Build a read-only inspection tool that exposes the full internal structure of
AAF files at both layers, with a CLI as the Phase 1 surface. Phase 2 (web GUI)
is out of scope for this brief.

For project-level context (problem, architecture, philosophy), read
`docs/PROJECT_OVERVIEW.md` first. For coding rules and invariants, read
`CLAUDE.md` at the repo root. This brief covers only the Phase 1 build.

## Phase 1 scope

In:

- Project scaffold at `/Users/amohr/programming/AAF_Browser/`
- `aafbrowser.core` library — CFB walker, AAF object walker, serializer,
  reference resolver
- `aafbrowser.cli` — Click-based CLI registered as `aafbrowser` console script
- Five CLI commands: `tree`, `dump`, `cfb`, `inspect`, `find`
- Tests with programmatically-generated fixtures
- `aafbrowser.web` skeleton (empty package, stub `app.py` raising
  NotImplementedError)

Out:

- Web GUI implementation (Phase 2)
- Editing or creation of AAF files (never)
- Format conversion
- Editorial analysis

## Verified API surface (use this, don't guess)

These have been confirmed against pyaaf2 1.7.1. Implement against them
directly; if any of these are wrong on the target machine, stop and report
rather than papering over it.

### CFB layer (via `aaf2.open(...).cfb`)

```python
import aaf2

with aaf2.open(path, 'r') as f:
    # f.cfb is a CompoundFileBinary instance
    # f.cfb.walk() yields (path: str, storages: List[DirEntry], streams: List[DirEntry])
    for path, storages, streams in f.cfb.walk():
        ...
```

`DirEntry` interface (verified):

- `name: str`
- `byte_size: int` (0 for storages)
- `class_id: AUID` (16-byte UUID; meaningful for storages — identifies AAF class)
- `path() -> str` (method, e.g. `/MetaDictionary-1/...`)
- `isfile() -> bool`, `isdir() -> bool`, `isroot() -> bool`
- `open('r') -> Stream` (Stream supports `seek(offset)` + `read(n)`)

The CFB tree is far larger than expected. Even an empty AAF contains thousands
of streams from MetaDictionary, ClassDefinitions, and TypeDefinitions
subtrees. The default CFB output MUST exclude `MetaDictionary-1` unless an
opt-in flag is passed. See command spec below.

### AAF object layer (via `aaf2.core.AAFObject`)

```python
from aaf2.core import AAFObject

# obj.properties() yields Property instances
for p in obj.properties():
    p.name           # str
    type(p).__name__ # 'Property' | 'StrongRefProperty' | 'StrongRefVectorProperty' | 'StrongRefSetProperty' | 'WeakRefProperty'
    p.value          # type depends on Property class — see serialization table

# Mob lookup
mob = f.content.mobs.get(mob_id)  # returns Mob or None

# Top-level entry points
f.header        # Header object
f.content       # ContentStorage object (has .mobs)
f.dictionary    # Dictionary object
f.root          # AAFObject root
f.metadict      # MetaDictionary
```

### File mode (read-only enforcement)

When opened with `'r'`:

- `f.mode == 'rb'`
- `f.writeable == False`

Tests must additionally compare SHA-256 of the input file before and after
every CLI command.

## Property serialization spec

`aafbrowser.core.serialize` converts Property values to JSON-friendly form
with type tags. Every output value is either a primitive (string, int, float,
bool, null), a list, or an object. Objects of types other than plain
dictionaries get a `_type` tag.

| Property class | `value` type | Output |
|---|---|---|
| `Property` (scalar) | `str` | the string |
| `Property` | `int`, `bool`, `None` | the value |
| `Property` | `float` | the value |
| `Property` | `datetime.datetime` | `{"_type": "datetime", "value": "<ISO8601>"}` |
| `Property` | `AAFRational` | `{"_type": "rational", "num": <int>, "den": <int>, "value": <float>}` |
| `Property` | `MobID`/`AUID`/`UMID` (URN string from pyaaf2) | `{"_type": "<auid|mobid|umid>", "value": "<urn>"}` |
| `Property` | `bytes` | `{"_type": "bytes", "length": <int>, "base64": "<...>"}` (truncate to 1KB by default; flag `--full-bytes` opts in) |
| `StrongRefProperty` | `AAFObject` | recurse → nested object |
| `StrongRefVectorProperty` | `List[AAFObject]` | recurse each → array |
| `StrongRefSetProperty` | iterable of `AAFObject` | recurse each → array (preserve iteration order) |
| `WeakRefProperty` | reference to dictionary entry | `{"_type": "weakref", "target_class": "<classname>", "target_name": "<name>", "target_auid": "<urn>"}` — do NOT recurse into the dictionary |

Each AAFObject node serializes to:

```json
{
  "_type": "aaf_object",
  "class": "<classname>",
  "mob_id": "<urn or null>",
  "name": "<str or null>",
  "properties": { "<PropertyName>": <value>, ... },
  "_cycle": false
}
```

If recursion encounters an already-visited object (`id(obj)` seen), emit a
cycle marker:

```json
{
  "_type": "aaf_object_cycle",
  "class": "<classname>",
  "mob_id": "<urn or null>"
}
```

## CLI command spec

All commands accept exactly one positional argument: the path to an AAF file.
All commands exit 0 on success, non-zero on error, and print errors to stderr.

### `aafbrowser tree <file.aaf>`

Pretty indented tree of the AAF object graph rooted at `f.content`. Each line
shows class + name + key identifying property (mob_id, slot_id, etc.) plus a
truncated summary of scalar properties. Cycles emit a marker line. Default
human output. No JSON option here — use `dump` for that.

### `aafbrowser dump <file.aaf> [--json] [--object-only] [--cfb-only] [--full-bytes]`

Full structural dump.

- Default: both layers, human-readable verbose form
- `--json`: machine-readable JSON to stdout (omit ANSI, omit progress)
- `--object-only`: skip CFB layer
- `--cfb-only`: skip AAF object layer
- `--full-bytes`: include full base64 of binary stream payloads (off by
  default to keep output tractable)

JSON shape:

```json
{
  "file": "<path>",
  "sha256": "<hex>",
  "aaf": <root AAFObject serialization, starting from f.content>,
  "cfb": <CFB tree>
}
```

### `aafbrowser cfb <file.aaf> [--include-metadict] [--show-bytes <stream-path> [--offset N] [--length N]] [--json]`

Raw CFB tree. By default excludes the `MetaDictionary-1` subtree (otherwise
output is 1000+ lines on every file). Use `--include-metadict` to opt in.

Each entry shows: name, type (storage/stream), byte_size for streams,
class_id for storages, full path.

`--show-bytes <stream-path>` switches mode: instead of tree output, dump a hex
view of that single stream. `--offset` and `--length` slice; default is full
stream up to 4KB, with a notice if truncated.

`--json` emits the tree as JSON instead of human-readable form.

### `aafbrowser inspect <file.aaf> --mob-id <id> | --path <slash/separated>`

Detailed dump of a single object with one level of reference resolution.
Cross-references display the target's class, name, and id but do not recurse
further.

`--mob-id` accepts either a full URN (`urn:smpte:umid:...`) or a bare hex
form; resolve via `f.content.mobs.get(...)`.

`--path` walks AAF property names from `f.content`, e.g.
`Mobs/<mob-id>/Slots/0/Segment`.

Default human output; `--json` for machine-readable.

### `aafbrowser find <file.aaf> --pattern <regex> [--in <names|values|both>] [--layer <aaf|cfb|both>] [--json]`

Regex search across the AAF graph and/or CFB tree.

- `--in names`: match Property names only
- `--in values`: match scalar property values only (string-coerced)
- `--in both`: default — match either
- `--layer aaf|cfb|both`: scope; default `both`. CFB matching searches
  storage/stream names and class_ids.
- Output per match: full path within the file, classname, property name,
  truncated value, and offset within the source.

This is the validation command for the Password channel-identity question.
Example invocation that should answer it on a real iso AAF:

```
aafbrowser find samples/password.aaf \
  --pattern '(?i)channel|chan_?id|physicaltrack|isolat|cam(era)?_?\d|mic_?\d'
```

## Acceptance criteria

1. `pip install -e .` from repo root installs the package and registers the
   `aafbrowser` console script.
2. All five CLI commands run on a fixture AAF and exit 0.
3. `aafbrowser dump fixture.aaf --json | python -m json.tool` succeeds —
   output is valid JSON.
4. `aafbrowser cfb fixture.aaf` excludes the MetaDictionary subtree;
   `--include-metadict` includes it.
5. `aafbrowser cfb fixture.aaf --show-bytes <some-stream>` produces a hex
   view of that stream's first 4KB by default.
6. `aafbrowser inspect fixture.aaf --mob-id <fixture-mob-id>` resolves and
   shows the mob's properties.
7. `aafbrowser find fixture.aaf --pattern <something-known-present>`
   returns at least one match with path + classname.
8. Cycle test: a synthetic case (or the dictionary's natural cycles) emits
   cycle markers, never infinite-loops.
9. Read-only test: SHA-256 of the input fixture is identical before and
   after running each command.
10. Type-tag round-trip: serializing an AAFRational produces a value with
    `_type: "rational"`; serializing a datetime produces `_type: "datetime"`.
11. Tests pass: `pytest` exits 0.

## Project layout (target)

```
/Users/amohr/programming/AAF_Browser/
├── CLAUDE.md                       # Pre-existing — see repo root
├── README.md                       # Create — install + usage examples
├── pyproject.toml                  # Create — project metadata, deps, console script
├── .gitignore                      # samples/, .venv/, __pycache__, *.egg-info, etc.
├── aafbrowser/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── cfb.py
│   │   ├── aaf.py
│   │   ├── serialize.py
│   │   └── resolver.py
│   ├── cli/
│   │   ├── __init__.py
│   │   └── __main__.py
│   └── web/
│       ├── __init__.py
│       └── app.py                  # Stub — raise NotImplementedError("Phase 2")
├── tests/
│   ├── __init__.py
│   ├── fixtures/                   # No checked-in AAFs; built by conftest
│   ├── conftest.py                 # Build fixture AAFs programmatically
│   ├── test_serialize.py
│   ├── test_cfb.py
│   ├── test_aaf.py
│   ├── test_resolver.py
│   ├── test_cli.py
│   └── test_readonly.py            # Hashes pre/post for every command
├── samples/                        # Gitignored — drop real AAFs here
└── docs/
    ├── PROJECT_OVERVIEW.md         # Pre-existing
    └── phase1-brief.md             # This file
```

## Build sequence

Commit at each step so we can roll back cleanly.

1. Scaffold: `pyproject.toml`, package layout, `.gitignore`, `README.md`
   stub, console-script registration.
2. `aafbrowser.core.serialize`: type-tagging serializer, with tests covering
   each value type from the spec table.
3. `aafbrowser.core.aaf`: AAFObject walker with cycle detection, with tests
   on a programmatically-built fixture.
4. `aafbrowser.core.cfb`: CFB walker, with default MetaDictionary exclusion,
   with tests.
5. `aafbrowser.core.resolver`: MobID and `--path` resolution, with tests.
6. `aafbrowser.cli.__main__`: each CLI command in turn — `tree`, `dump`,
   `cfb`, `inspect`, `find` — with CLI tests using `click.testing.CliRunner`.
7. `aafbrowser.web` skeleton: empty package + stub `app.py`.
8. `test_readonly.py`: SHA-256 round-trip tests for every command.
9. README usage examples.
10. Final `pytest` run, lint, completion report.

## Concerns to flag rather than paper over

- If pyaaf2's `f.cfb.walk()` API differs from what's specified above on the
  target environment (different version, etc.), stop and report rather than
  improvising.
- If a property value comes through with an unexpected type not covered by
  the serialization table, fall back to `{"_type": "unknown", "repr": <repr>,
  "python_type": "<typename>"}` and emit a warning to stderr — never silently
  drop data.
- If a fixture AAF cannot be built without writing audio essence, that's
  fine; use empty PCMDescriptors as the `pyaaf2` examples do. The
  programmatic-fixture test in this brief was already verified to work.

## Completion report

When Phase 1 lands, produce a report covering:

- What was built (matched against acceptance criteria checklist).
- Any deviations from this brief, with rationale.
- Findings from running `aafbrowser find` against any real `samples/` AAFs
  (especially Password iso AAFs, if available) — relevant matches and where
  they appeared.
- Recommendations for the Phase 2 web GUI brief: which surfaces of the core
  felt incomplete during CLI use, and what the GUI should prioritize beyond
  what the CLI already does well.
