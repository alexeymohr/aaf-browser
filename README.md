# AAF Browser

Read-only inspection tool for AAF (Advanced Authoring Format) files. Exposes
both layers of an AAF — the raw CFB (Microsoft Compound File Binary)
storage tree and the AAF object graph (Mobs, Slots, Components, Descriptors,
WeakRefs into the dictionary, etc.) — so a developer can answer questions
like *"is this metadata actually present in the file or am I being lied to
by some intermediate tool?"*

## Install — macOS app (recommended)

Download the latest `AAF-Browser-vX.Y.Z-arm64.dmg` from the
[GitHub Releases](https://github.com/alexeymohr/aaf-browser/releases)
page, mount it, and drag **AAF Browser** to `/Applications`. Launch
from Spotlight or the Applications folder. The app opens in a real
macOS window — title bar, dock icon, native menu bar with Cmd-Q to
quit. No browser tab.

Apple Silicon (M1+) only. Files are never modified — the app uses
the same read-only pyaaf2 path as the CLI. The window's content is
the same web GUI that pip-installed users see; the difference is
that it renders inside an embedded WKWebView instead of in
Safari/Chrome.

The .dmg is signed + notarized + stapled, so the first launch
proceeds without a Gatekeeper warning.

## Install — pip (developers / CLI)

For the command-line tools (`aafbrowser tree`, `dump`, `cfb`,
`inspect`, `find`, `walk`, `web`):

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
aafbrowser web /path/to/session.aaf

# Custom host/port, no auto-open
aafbrowser web --host 0.0.0.0 --port 8080 --no-browser session.aaf
```

What the GUI does:

- **Tracks** is the default landing tab — a post-production-sound-operator
  view of the file. Three columns: ordered list of audio + video tracks
  on the topmost CompositionMob (left); clips on the selected track,
  each labeled with its recovered recorder/mic identity from the chain
  walk (center); shared inspector with operator-summary header on top
  of the existing object dump (right). Each clip row shows timeline
  position, length, the source MasterMob, and — when present — the
  named recorder SourceMob (e.g. `PW_310_ISO1_B`). Clip rows expand to
  reveal the underlying AAF object structure.
- **All Mobs** and **CFB** tabs sit alongside Tracks for the geek-view
  paths: a flat Mob list grouped by class with name filter (All
  Mobs), and the raw CFB storage tree (CFB). The inspector is shared
  across all four views — selecting something in Tracks and switching
  to another tab leaves the inspector showing what you last clicked.
- **Sources** tab is the cross-track pull list: a
  deduplicated roster of every recorder source mob in the file,
  sorted most-used-first, with use-count, format (sample rate · bit
  depth · channels), and online/offline indicator dots when
  Locator URLs are present. Selecting a source shows a "Used by"
  panel that jumps directly to the corresponding clip in the
  Tracks view. First load takes a few seconds (the server walks
  every clip to compute use counts); subsequent visits are
  instant.
- Each clip row in the **Tracks** view also surfaces, in addition
  to the recovered mic identity, its head/tail handle (frames +
  seconds), per-clip audio format, and an offline indicator
  when its source file path doesn't exist on disk. Multi-input
  combiner clips (e.g. an Avid mix-down of two mics) expand into
  one sub-clip per input, each with its own recovered identity.
- Format-aware recovery: the inspector exposes a
  `recovery_status` per clip — **recoverable** for normal Avid
  chain-walks, Premiere stereo splits (Mono Audio Pan) and
  combiners whose inputs all resolve; **ambiguous** for
  partial-information cases; **unrecoverable** for Premiere
  multichannel polywav imports (channel index destroyed at import)
  and broken/non-source terminals. Unrecoverable clips get a faint
  red border-left in the tree so the operator sees at a glance
  that the mic name shouldn't be trusted. Premiere stereo-split
  tracks get an `L`/`R` pill next to the track name.
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
- **Open** uses a native file picker. In the bundled .app it's a real
  `NSOpenPanel` via pywebview's JS bridge (with `.aaf` filtering); in
  `aafbrowser web` from a pip install it's an `osascript`-driven
  picker. Non-macOS platforms fall back to a paste-the-path dialog.
- **Quit** link in the topbar stops the local server cleanly. In a
  browser tab, closing the tab also stops it (via
  `navigator.sendBeacon`). In the bundled .app, closing the window
  or Cmd-Q from the menu bar shuts everything down.
- The window/tab title shows the open file's basename
  (`AAF Browser — PWD_310_LC_10-07-2025.aaf`).

The GUI is a thin frontend over the same `aafbrowser.core` library the
CLI uses. Read-only invariants are enforced server-side; pyaaf2 access
is serialized through a single `threading.Lock`. The bundled .app
ships an embedded WKWebView via pywebview, so the window is native
even though the content is the same Flask + JS frontend.

### `.aaf` file association (macOS app only)

The bundled .app declares a `.aaf` file association via
`CFBundleDocumentTypes`. Right-click an .aaf in Finder → **Open With
→ AAF Browser** preloads the file when the app starts cold. Once the
app is already running, double-clicking another .aaf in Finder won't
preload it (would require an in-process AppKit handler not present
in v1) — use the in-app **Open** button instead.

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

# Restrict the AAF-layer walk to one or more Mob classes (repeatable)
aafbrowser find session.aaf --pattern KEKE --class CompositionMob
aafbrowser find session.aaf --pattern host --class CompositionMob --class MasterMob

# JSON output (one match per line)
aafbrowser find session.aaf --pattern '(?i)channel' --json
```

The `--class` filter materially speeds up search on large sessions: on a
4,648-Mob iso AAF, `--class CompositionMob` cuts a 7 s search to 3.5 s
(50% off); `--class MasterMob` cuts it to 1.7 s (76% off).

This is the validation command for questions like *"does this AAF contain
any property or stream that distinguishes physical recorder channels?"*.
Example pattern targeting iso-recorder identity:

```sh
aafbrowser find samples/password.aaf \
  --pattern '(?i)channel|chan_?id|physicaltrack|isolat|cam(era)?_?\d|mic_?\d'
```

### `aafbrowser walk <file.aaf> --mob-id <id> | --path <path>`

Walk the SourceClip chain hop-by-hop, returning the per-hop
`(mob_class, mob_name, slot_id, segment_class, physical_track_number,
edit_rate, terminal, terminal_reason)` tuple. The deepest-named-mob in
the chain is typically the original capture (the WAV file, the camera
clip), which answers the iso-channel question for any clip.

```sh
# From a Mob (slot defaults to the first; override with --slot)
aafbrowser walk session.aaf --mob-id "urn:smpte:umid:..."
aafbrowser walk session.aaf --mob-id "urn:smpte:umid:..." --slot 2

# From a property path (e.g. land directly on a SourceClip)
aafbrowser walk session.aaf --path "Mobs/<urn>/Slots/0/Segment"

# Bound the walk depth (default 64; clipped chains terminate
# with reason "max_hops_reached")
aafbrowser walk session.aaf --mob-id "..." --max-hops 4

# Machine-readable
aafbrowser walk session.aaf --mob-id "..." --json
```

Sample output (3-hop chain — MasterMob → SourceMob → SourceMob):

```
  [0] MasterMob 'PW VO 310 B Round 1.wav.new.05' slot=1 segment=SourceClip edit=30000/1001
  [1] SourceMob slot=1 segment=SourceClip edit=30000/1001
* [2] SourceMob 'PW VO 310 B Round 1.wav' slot=1 segment=SourceClip edit=30000/1001  -- terminal: no_source_id
```

Terminal reasons cover every non-chainable shape: `essence`,
`no_source_id`, `broken_ref`, `cycle`, `filler`, `timecode`,
`essence_group`, `pulldown`, `operation_group`,
`multi_segment_sequence`, `empty_sequence`, `max_hops_reached`,
`invalid_slot`, `non_clip_segment:<class>`. The walker is cycle-safe
(visited `(mob_id, slot_id)` set).

### `aafbrowser session <file.aaf>`

Headline summary of the file: composition, track + clip + mob counts,
audio specs, timecode, authoring (with detected NLE kind), duration.
Mirrors the GUI's session bar; `--json` matches `/api/session`.

```sh
aafbrowser session session.aaf
aafbrowser session session.aaf --json | jq .session.timecode
```

### `aafbrowser tracks <file.aaf>`

Audio + video tracks on the topmost CompositionMob, with PT-style
A1/V1 ordinals and clip counts. Premiere stereo-split tracks show
`[L]`/`[R]` next to the name.

```sh
aafbrowser tracks session.aaf
aafbrowser tracks session.aaf --json
```

### `aafbrowser clips <file.aaf> --slot N`

Clips on a single track: timeline timecode, length, recovered mic
identity from the chain-walk, recovery_status (format-aware
classification), and sub-clip fan-out for multi-input combiners.

```sh
aafbrowser clips session.aaf --slot 3
aafbrowser clips session.aaf --slot 3 --json | jq '.clips[] | select(.recovery_status == "unrecoverable")'
```

### `aafbrowser sources <file.aaf>`

Cross-track deduplicated source-mob pull list with per-mob use
counts. Defaults to "used" sources (use_count > 0); `--unused`
includes everything in the file. Mirrors the GUI's Sources tab.

```sh
aafbrowser sources session.aaf
aafbrowser sources session.aaf --json | jq '.sources[] | {name, use_count}'
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
│   ├── resolver.py # MobID / path / regex search (with mob_class filter)
│   └── chain.py    # Multi-hop SourceClip chain walker
├── cli/            # Click-based CLI (`aafbrowser` console script)
└── web/            # Flask app + vanilla JS GUI
    ├── app.py      # Routes under /api
    ├── state.py    # Process-global open file + threading.Lock
    └── static/     # index.html, styles.css, app.js
```

`aafbrowser.core` is pure: it has no dependency on Click, Flask, or terminal
formatting. CLI and web are thin adapters.
