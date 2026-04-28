# AAF Browser

Read-only inspection tool for AAF (Advanced Authoring Format) files. Exposes
both layers of an AAF — the raw CFB (Microsoft Compound File Binary)
storage tree and the AAF object graph (Mobs, Slots, Components, Descriptors,
WeakRefs into the dictionary, etc.) — so a developer can answer questions
like *"is this metadata actually present in the file or am I being lied to
by some intermediate tool?"*

See [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) for context, and
the briefs at [docs/phase1-brief.md](docs/phase1-brief.md) (CLI) and
[docs/phase2-brief.md](docs/phase2-brief.md) (web GUI).

## Install

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

This registers the `aafbrowser` console script.

## Read-only — non-negotiable

Every command opens the input AAF with `aaf2.open(path, 'r')` and refuses to
proceed if pyaaf2 returns a writeable handle. The test suite hashes the
input before and after every command and asserts equality.

## Commands

### `aafbrowser tree <file.aaf>`

Indented tree of the AAF object graph rooted at `f.content`. Strong-ref
children recurse, WeakRefs render as `WeakRef -> ClassName "name"` (no
recursion into the dictionary).

```sh
aafbrowser tree session.aaf
```

### `aafbrowser dump <file.aaf>`

Full structural dump of both layers (AAF object graph + CFB tree).

```sh
# Human form, both layers
aafbrowser dump session.aaf

# Machine-readable JSON (round-trip safe — every typed value carries _type)
aafbrowser dump session.aaf --json > session.json

# Skip a layer
aafbrowser dump session.aaf --object-only
aafbrowser dump session.aaf --cfb-only

# Include full base64 of binary stream payloads (default truncates to 1 KB)
aafbrowser dump session.aaf --json --full-bytes > full.json
```

JSON shape:

```json
{
  "file": "/abs/path/to/session.aaf",
  "sha256": "<hex of input>",
  "aaf": { "_type": "aaf_object", ... },
  "cfb": { "_type": "cfb_storage", ... }
}
```

### `aafbrowser cfb <file.aaf>`

Raw CFB storage tree. The `MetaDictionary-1` subtree is filtered by default
(it contains thousands of schema entries that drown out anything
interesting); a single summary line records its size.

```sh
# Default — readable
aafbrowser cfb session.aaf

# Include the full MetaDictionary subtree
aafbrowser cfb session.aaf --include-metadict

# Hex view of a single stream (lazy — only reads the requested range)
aafbrowser cfb session.aaf --show-bytes "/MasterMob-1234/properties" \
  --offset 0 --length 256

# JSON tree
aafbrowser cfb session.aaf --json > cfb.json
```

### `aafbrowser inspect <file.aaf> --mob-id <id> | --path <path>`

Dump a single object with one level of reference resolution. Cross-references
to dictionary entries display the target's class and name without recursing
further.

```sh
# By MobID — accepts URN, dotted-hex, or plain-hex
aafbrowser inspect session.aaf --mob-id "urn:smpte:umid:060a2b34..."
aafbrowser inspect session.aaf --mob-id "060a2b340101..."

# By property path from f.content
aafbrowser inspect session.aaf --path "Mobs/<mob-id>/Slots/0/Segment"

# JSON
aafbrowser inspect session.aaf --mob-id <id> --json
```

### `aafbrowser web [<file.aaf>]`

Starts a local browser-based GUI on `127.0.0.1:5173` (override with
`--host` and `--port`). If a file path is given, the server opens it
before serving so the page lands on the file already loaded.

```sh
# Open a file and launch the GUI in the system browser
aafbrowser web samples/Password_Mix_Audio_Tracks.aaf

# Custom host/port, no auto-open
aafbrowser web --host 0.0.0.0 --port 8080 --no-browser session.aaf
```

What the GUI does:

- Two-pane layout. Left pane: AAF Mob list (grouped by class, with name
  filter) and CFB storage tree. Right pane: type-aware inspector with
  breadcrumb navigation.
- Click a MobID badge anywhere in the inspector to jump to that Mob in
  the list (works across StrongRefVector members and scalar MobID
  values).
- Click a WeakRef to expand it in place — dictionary entries aren't
  surfaced as Mobs, so they show their identifying triple
  (target_class / target_name / target_auid) without recursing.
- CFB streams switch the inspector to a hex viewer with offset/length
  controls. Storage rows show the metadict-decoded class name (e.g.
  `[MasterMob]`) where pyaaf2 has it registered.
- Search box at the top of the page runs a regex against names, values,
  or both, on the AAF graph, the CFB tree, or both. Results render in a
  collapsible bottom panel; clicking a row navigates to the Mob (AAF
  layer) or expands the CFB ancestors and selects the entry (CFB
  layer).

The GUI is a thin frontend over the same `aafbrowser.core` library the
CLI uses. Read-only invariants are enforced server-side; pyaaf2 access
is serialized through a single `threading.Lock`.

### `aafbrowser find <file.aaf> --pattern <regex>`

Regex search across the AAF object graph and/or CFB tree. Each match prints
the full property path, classname, field, and a truncated value.

```sh
# Find anything that mentions "channel" — case-insensitive
aafbrowser find session.aaf --pattern '(?i)channel'

# Limit to property names (skip stringified scalar values)
aafbrowser find session.aaf --pattern 'MobID' --in names

# Limit to the CFB layer (storage / stream names + class_ids)
aafbrowser find session.aaf --pattern 'MasterMob' --layer cfb

# JSON output (one match per line)
aafbrowser find session.aaf --pattern '(?i)channel' --json
```

This is the validation command for questions like *"does this AAF contain
any property or stream that distinguishes physical recorder channels?"*.
Example pattern targeting iso-recorder identity:

```sh
aafbrowser find samples/password.aaf \
  --pattern '(?i)channel|chan_?id|physicaltrack|isolat|cam(era)?_?\d|mic_?\d'
```

## Type-tagged JSON

Every JSON output preserves type information via a `_type` discriminator so
that values which don't round-trip cleanly through plain JSON (datetimes,
rationals, AUIDs, MobIDs, bytes) can be reconstructed by a consumer:

```json
{ "_type": "rational",  "num": 48000, "den": 1001, "value": 47.952 }
{ "_type": "datetime",  "value": "2026-04-27T12:34:56" }
{ "_type": "auid",      "value": "01030202-0200-0000-060e-2b3404010101" }
{ "_type": "mobid",     "value": "urn:smpte:umid:060a2b34..." }
{ "_type": "bytes",     "length": 4096, "base64": "...", "truncated": false }
{ "_type": "weakref",   "target_class": "DataDef",
                        "target_name": "DataDef_Sound",
                        "target_auid": "..." }
```

## Tests

```sh
pip install -e '.[test]'
pytest
```

The test suite covers the serializer, the AAF walker (with cycle detection),
the CFB walker (with default MetaDictionary filtering and run collapsing),
the resolver (URN/hex MobIDs, slash-separated paths), every CLI command,
and the read-only invariant.

## Architecture

```
aafbrowser/
├── core/           # Pure library — CLI and web both consume this
│   ├── cfb.py      # CFB walker (uses pyaaf2's f.cfb, NOT olefile)
│   ├── aaf.py      # AAF object graph walker with cycle detection
│   ├── serialize.py # Property values -> JSON-friendly with type tags
│   └── resolver.py # MobID / path / regex search
├── cli/            # Click-based CLI (`aafbrowser` console script)
└── web/            # Phase 2: Flask app + vanilla JS GUI
    ├── app.py      # Routes under /api
    ├── state.py    # Process-global open file + threading.Lock
    └── static/     # index.html, styles.css, app.js
```

`aafbrowser.core` is pure: it has no dependency on Click, Flask, or terminal
formatting. CLI and web are thin adapters.
