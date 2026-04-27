# AAF Browser

Read-only inspection tool for AAF (Advanced Authoring Format) files. Exposes
both the raw CFB (Microsoft Compound File Binary) storage tree and the AAF
object graph, so you can answer questions like *"is this metadata actually
present in the file or am I being lied to by some intermediate tool?"*

See [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) for context and
[docs/phase1-brief.md](docs/phase1-brief.md) for the build brief.

## Install

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick usage

Detailed examples are below. All commands are read-only — input files are
never modified.

```sh
aafbrowser tree path/to/file.aaf
aafbrowser dump path/to/file.aaf --json > file.json
aafbrowser cfb path/to/file.aaf
aafbrowser inspect path/to/file.aaf --mob-id <mob-id>
aafbrowser find path/to/file.aaf --pattern '(?i)channel'
```

## Tests

```sh
pip install -e '.[test]'
pytest
```
