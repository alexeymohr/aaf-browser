# AAF Browser — Project Overview

## What this is

A read-only inspection tool for AAF (Advanced Authoring Format) files. AAF is
the structured-storage container Avid Media Composer and Pro Tools use to
exchange editorial sessions. AAF files contain a typed object graph (Mobs,
Slots, Components, Descriptors, Locators, etc.) on top of a Microsoft
Compound File Binary (MS-CFB) container.

AAF Browser exposes both layers — the raw CFB storage/stream tree and the AAF
object graph — so a developer can answer questions like:

- "Is this metadata actually present in the file, or is some intermediate tool
  dropping it?"
- "What does the structure of this file actually look like, beyond what my
  current Python script reads?"
- "Which AAF object does *this* CFB stream correspond to?"

## Why we're building it

AAF Browser exists because of a concrete problem in adjacent work:

While building TrackManager (part of FirstPass / pt-mix-tool), we hit a wall
identifying iso clips in Password show AAFs. The show records audio direct to
cameras (referred to as ISO1, ISO2, etc.), with multiple channels per camera.
Multiple Pro Tools tracks end up labeled "ISO1" with no obvious way to
distinguish, e.g., the host Mike from the contestant Mike at the editorial
clip level.

A prior Claude Code investigation claimed channel identity is encoded in some
"sub-folder structure" inside the AAF file. That phrasing strongly implies the
raw CFB layer rather than the pyaaf2-surfaced object model. We need to verify
that claim independently — by directly inspecting the structure rather than
trusting any tool's interpretation.

The first concrete validation case for the tool is therefore: load a Password
iso AAF, locate any property or stream that distinguishes channels, and either
recover the mapping or prove it isn't there.

The tool is general-purpose, though. Any future AAF investigation — different
shows, different recorders, different NLEs — can use the same CLI and (future)
web GUI.

## What it does

Phase 1 (CLI):

- `aafbrowser tree <file.aaf>` — readable indented tree of the AAF object graph
- `aafbrowser dump <file.aaf> [--json]` — full structural dump of both layers
- `aafbrowser cfb <file.aaf>` — raw CFB storage/stream tree (filtered by default)
- `aafbrowser inspect <file.aaf> --mob-id <id>` — single-object detail with
  one level of reference resolution
- `aafbrowser find <file.aaf> --pattern <regex>` — regex search across
  property names and values, both layers — the validation command for the
  Password question

Phase 2 (web GUI, separate brief): two-pane interactive browser with
click-to-navigate references on top of the same core library.

## What it does not do

- AAF editing or creation — read-only, full stop
- Format conversion to OMF, EDL, etc.
- Audio playback or waveform display
- Editorial-level analysis (clip sorting, classification, automation
  reconstruction — that's TrackManager / FirstPass territory)

## Architecture

Single namespace package, three submodules, sharing a core library:

```
AAF_Browser/
├── CLAUDE.md                  # Project rules for Claude Code sessions
├── README.md                  # User-facing install + usage
├── pyproject.toml
├── .gitignore
├── aafbrowser/
│   ├── __init__.py
│   ├── core/                  # Pure library — both CLI and web consume this
│   │   ├── __init__.py
│   │   ├── cfb.py             # Raw CFB walker (uses pyaaf2's f.cfb)
│   │   ├── aaf.py             # AAF object graph walker (pyaaf2)
│   │   ├── serialize.py       # Property values → JSON-friendly with type tags
│   │   └── resolver.py        # MobID and reference lookup
│   ├── cli/
│   │   ├── __init__.py
│   │   └── __main__.py        # `aafbrowser` console script
│   └── web/                   # Phase 2 — empty stub for Phase 1
│       ├── __init__.py
│       └── app.py
├── tests/
│   ├── fixtures/              # Small AAFs built programmatically by setup
│   └── ...
├── samples/                   # Real-world AAFs (gitignored)
└── docs/
    ├── PROJECT_OVERVIEW.md    # This file
    └── phase1-brief.md        # Phase 1 build instructions (frozen on land)
```

`aafbrowser.core` is pure: it has no dependency on Click, Flask, or terminal
formatting. CLI and web are thin adapters.

## Tech stack

- Python 3.11+
- `pyaaf2` (1.7+) — AAF object model and built-in CFB reader
- `click` — CLI framework
- `pytest` — tests
- Phase 2 (later): Flask + vanilla HTML/CSS/JS, no build step

We deliberately do not use `olefile`. pyaaf2 ships its own
`CompoundFileBinary` walker on the already-open file handle, so a second CFB
parser is unnecessary and would complicate the read-only guarantee.

## Phasing

| Phase | Scope | Status |
|---|---|---|
| 1 | Core library + CLI | Shipped — `docs/phase1-brief.md`, `docs/completion_reports/phase1-completion-report.md` |
| 2 | Web GUI on top of the same core | Shipped — `docs/phase2-brief.md`, `docs/completion_reports/phase2-completion-report.md` |
| 3 | Chain-walk + class-filtered find | Shipped — `docs/phase3-brief.md`, `docs/completion_reports/phase3-completion-report.md` |
| 4 | macOS app distribution + native file picker | Shipped — `docs/phase4-brief.md`, `docs/completion_reports/phase4-completion-report.md` |
| 5 | Embedded WKWebView (real Mac app) | Shipped — `docs/phase5-brief.md`, `docs/completion_reports/phase5-completion-report.md` |
| 6 | Operator-first browser layer (Tracks default landing) | Shipped — `docs/phase6-brief.md`, `docs/completion_reports/phase6-completion-report.md` |

Each phase brief was drafted only after the previous phase had been used in
anger against at least one real AAF (the Password show is the recurring test
material). Real usage drives what the next phase prioritizes.

Phase 3 produced `docs/identifying-clip-channels.md` — a self-contained
explainer for the TrackManager session on how to use the chain-walk to
identify a clip's true physical microphone channel through the
SourceClip → MasterMob → SourceMob chain.

## Read-only as an invariant

This deserves its own section. Every operation must leave input files
byte-identical. This is enforced at three levels:

1. pyaaf2 file mode is always `'r'` (`f.mode == 'rb'`, `f.writeable == False`).
2. Tests hash input files before and after each CLI command and assert
   equality.
3. The architecture has no write paths. Any future feature that seems to
   require writing is a sign to stop and re-scope.

## Key references

- pyaaf2 documentation: https://pyaaf.readthedocs.io/
- AAF Object Specification: SMPTE ST 377-1 (and the AAF Edit Protocol)
- MS-CFB (Compound File Binary Format): the underlying container format
- Adjacent project: `~/pt-mix-tool/` (FirstPass / TrackManager) — the project
  whose investigation needs surfaced this tool
