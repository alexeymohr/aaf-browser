# AAF Browser — Phase 9 Brief

## Goal

Make the operator-first surface usable from a terminal as well as the
Mac app, and make the bundled `.dmg` notarized so first-launch
doesn't trigger a Gatekeeper warning for new users.

CLI parity work mirrors the Phase 6+7+8 operator endpoints
(`/api/session`, `/api/tracks`, `/api/track/clips`, `/api/sources`)
as new Click subcommands consuming the same `aafbrowser.core.operator`
module. Notarization is a separate dist-hygiene track that wires the
existing build script's `CODESIGN_IDENTITY` env var to the
`xcrun notarytool` flow.

For project-level context read `docs/PROJECT_OVERVIEW.md` and the
repo-root `CLAUDE.md`. Phases 1–8 are complete — see
`docs/completion_reports/`. This brief covers Phase 9 only.

## Why now

Phase 6+7+8 stabilized the operator-layer API. The web GUI is the
primary interface but a terminal-friendly surface unlocks scripting:
"give me a JSON dump of every clip on the HOST track of every
episode in this directory" type workflows. CLI parity is mechanical
once the core module is stable — no new primitives needed.

Notarization is the last remaining first-launch UX wart. Right now
new users see a Gatekeeper "couldn't be opened because Apple cannot
check it for malicious software" warning and have to right-click →
Open. A signed + notarized + stapled `.dmg` opens normally.

Both items are scoped tightly: ~150 lines of CLI code, ~50 lines of
build-script extension, ~80 lines of setup docs.

## Scope

**In:**

1. **Four new CLI subcommands**, mirroring the operator endpoints:
   - `aafbrowser session <file.aaf> [--json]` — headline summary
     (composition, track + clip + mob counts, audio specs,
     timecode, authoring, duration). Mirrors `/api/session`.
   - `aafbrowser tracks <file.aaf> [--json]` — track list with PT
     ordinal + slot id + kind + clip count. Mirrors `/api/tracks`.
   - `aafbrowser clips <file.aaf> --slot N [--json]` — clips on the
     given slot, with TC + length + mic identity + recovery_status.
     Mirrors `/api/track/clips`.
   - `aafbrowser sources <file.aaf> [--json]` — deduplicated source
     pull list with use_count. Mirrors `/api/sources`.
2. **Read-only invariant** extended to cover all four new commands
   in `tests/test_readonly.py`.
3. **Notarization workflow** in `packaging/macos/build.sh`:
   - Gated on `NOTARIZE=1` env var + `APPLE_ID`, `APPLE_TEAM_ID`,
     `APPLE_APP_PASSWORD` (or alternatively a stored credential
     profile via `notarytool store-credentials`).
   - Submits the signed `.dmg` to Apple's notary service, waits
     for the result (`--wait`), staples on success.
   - Local iter builds (without NOTARIZE=1) skip the step entirely;
     the existing ad-hoc-sign path is preserved.
4. **Setup docs** at `docs/notarization-setup.md` covering the
   one-time `notarytool store-credentials` setup so future builds
   can run unattended in CI.

**Out (deferred):**

- Conform / diff (excluded by user direction).
- A `aafbrowser walk` enhancement to use `walk_chain_tree` (Phase 7
  lift). The existing `walk` uses flat `walk_chain`; tree-walk for
  multi-input combiners is fine to add but isn't required for this
  phase.
- Rich text output formatting (tables with column alignment,
  ANSI colors, etc.). Output is plain text or JSON — operators who
  want fancy can pipe through `jq` / `column`.
- Sparkle / auto-update.
- GitHub Actions secrets wiring (the workflow file from Phase 4
  exists; documenting how to set Secrets is a one-line README
  bullet).

## Decisions to lock in

- **CLI commands echo a one-line file metadata header**
  (`aaf: <abs path>` + `sha256: <hash>`) before the main payload,
  matching the existing `dump` command convention. JSON output puts
  these in the envelope.
- **`--json` output is one JSON object per command** (not
  newline-delimited), and round-trips through `json.loads`. Keeps
  simple `cat … | jq .tracks[]` workflows working.
- **Errors raise `click.ClickException`** so the exit code is
  non-zero and stderr carries the message.
- **CLI commands import `core.operator` directly** (not via the
  web layer). No need to spin up Flask for terminal use.
- **Notarization is opt-in via env var.** Local builds stay fast
  by default. CI runs with `NOTARIZE=1` + secrets injected.

## Verified API surface (use this, don't guess)

`xcrun notarytool` was the modern replacement for `altool` in Xcode
13+. Two ways to authenticate:

```bash
# Option A: pass credentials each time
xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait

# Option B: store a credential profile once, reference by name
xcrun notarytool store-credentials "AC_PASSWORD" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD"
xcrun notarytool submit "$DMG_PATH" --keychain-profile "AC_PASSWORD" --wait
```

After successful notarization, staple the ticket so the .dmg works
offline:

```bash
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"   # sanity check
```

## Architectural shape

### CLI commands (`aafbrowser/cli/__main__.py`)

Each new command:
1. Calls `_file_sha256(aaf_path)` for the metadata header.
2. Opens via `_open_readonly(aaf_path)`.
3. Calls into `aafbrowser.core.operator` (`session_summary`,
   `list_tracks`, `list_clips`, `source_inventory`).
4. Renders human form by default, `--json` opts into the
   envelope shape.

```python
@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--json", "as_json", is_flag=True, ...)
def session(aaf_path, as_json):
    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        size = os.path.getsize(aaf_path)
        summary = operator.session_summary(f, file_size_bytes=size)
    if as_json:
        click.echo(json.dumps({
            "file": str(Path(aaf_path).resolve()),
            "sha256": sha,
            "session": summary.to_dict(),
        }, indent=2, ensure_ascii=False))
        return
    # Human form: 2-column key-value table
    ...
```

### Build script (`packaging/macos/build.sh`)

After the existing DMG-signing block, insert:

```bash
if [[ "${NOTARIZE:-0}" == "1" ]]; then
  echo "==> Notarizing"
  if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG_PATH" \
      --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  else
    xcrun notarytool submit "$DMG_PATH" \
      --apple-id "$APPLE_ID" \
      --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" \
      --wait
  fi
  echo "==> Stapling"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
fi
```

### Setup docs (`docs/notarization-setup.md`)

One-page guide covering:
- Apple Developer account + App-Specific Password generation
- `notarytool store-credentials` one-time setup
- Local-build env vars
- CI secrets (GitHub Actions)
- Troubleshooting common errors (rejected, hardened-runtime
  missing, entitlements wrong)

### What does NOT change

- `core/operator.py`, `core/chain.py`, etc. — all reused as-is.
- Web layer — unchanged.
- Existing CLI commands — unchanged.
- PyInstaller spec — unchanged.

## Files to be modified / created

- `aafbrowser/cli/__main__.py` (modified — 4 new commands)
- `tests/test_cli.py` (modified — coverage for the 4 new commands)
- `tests/test_readonly.py` (modified — extend the per-command loop)
- `packaging/macos/build.sh` (modified — notarization step)
- **NEW** `docs/notarization-setup.md`
- **NEW** `docs/phase9-brief.md` (this file)
- **NEW** `docs/completion_reports/phase9-completion-report.md`
- `docs/PROJECT_OVERVIEW.md` (modified — Phase 9 row)
- `README.md` (modified — CLI command section gets the new
  commands)

## Build sequence

1. **This brief** (`docs/phase9-brief.md`).
2. CLI: `aafbrowser session` (simplest, all data already in
   `operator.session_summary`).
3. CLI: `aafbrowser tracks`, `aafbrowser clips`,
   `aafbrowser sources`.
4. Tests: per-command unit tests (use `CliRunner`) + read-only
   invariant extension.
5. Notarization: extend `build.sh`, write
   `docs/notarization-setup.md`. (Doesn't require local
   credentials to land — the env-gated path stays inert without
   `NOTARIZE=1`.)
6. README + PROJECT_OVERVIEW updates.
7. Phase 9 completion report.

## Acceptance criteria

1. `pytest` exits 0; new tests cover all four commands in both
   human and JSON form.
2. Each new CLI command produces sensible output on
   `samples/PWD_310_LC_10-07-2025.aaf`.
3. JSON output for each command parses with `json.loads` and
   matches the corresponding `/api/...` endpoint's shape (modulo
   the envelope wrapper).
4. Read-only invariant: input file SHA-256 unchanged after running
   every new command.
5. `bash packaging/macos/build.sh` without `NOTARIZE=1` produces
   a working ad-hoc-signed .dmg as before (no behavior change).
6. With `NOTARIZE=1` + valid credentials, the resulting .dmg is
   stapled and `xcrun stapler validate` passes. (Tested on the
   user's machine; not exercised in CI without secrets.)

## Risks to flag rather than paper over

- **Pure-Python `os.path.getsize`** for the file size in the new
  CLI commands — same as the web `/api/session` endpoint. A symlink
  or special file could confuse it; use `Path(...).stat().st_size`
  defensively if needed.
- **JSON output for `tracks` / `clips` could be huge** on big files
  (PWD_310 has 5981 clips). For `clips` the size is bounded by the
  selected slot; for `tracks` it's small. No paginate flag in v1;
  add later if anyone hits the wall.
- **Notarization round-trip is slow** (Apple's queue, often ~1
  minute per submission). The build script `--wait`s synchronously.
  Acceptable for a release-build flow; documented.
- **App-Specific Password credentials in env** is the standard
  approach but means CI logs need to be careful not to echo them.
  The build script uses bash `set -e` + parameter substitution so
  passwords aren't logged.
- **No real notarization test in CI.** The user's local machine is
  the only place this gets exercised until secrets are configured.
  Documented.

## Completion report

When Phase 9 lands, produce
`docs/completion_reports/phase9-completion-report.md` covering:

- Acceptance-criteria checklist.
- CLI output samples on PWD_310 (one example per command).
- Read-only invariant verification.
- Notarization status (skipped on CI, documented for user).
- Phase 10+ candidate ideas (no commitment): Sparkle auto-update,
  GitHub Actions release workflow, AIFCDescriptor coverage,
  plugin-style operator workflows.
