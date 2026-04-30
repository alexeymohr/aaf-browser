# Notarization setup

Goal: a `.dmg` that opens on a fresh Mac without a Gatekeeper warning.

`packaging/macos/build.sh` already supports Developer-ID code signing
via `CODESIGN_IDENTITY`. Phase 9 added an opt-in notarization step
gated on `NOTARIZE=1`. This doc covers the one-time setup so future
builds notarize unattended.

## Prerequisites

- An Apple Developer account ($99/yr).
- A Developer ID Application certificate installed in your login
  keychain. Find its name via:
  ```bash
  security find-identity -v -p codesigning | grep "Developer ID Application"
  ```
- An app-specific password for `notarytool`. Generate at
  https://appleid.apple.com → "Sign-In and Security" → "App-Specific
  Passwords" → "+". Name it something like "aafbrowser notarization".
- Your Apple Team ID (10-char alphanumeric). Find at
  https://developer.apple.com → Membership → Team ID.

## One-time: store credentials

Recommended: stash the credentials in the keychain once so you don't
have to pass them every time.

```bash
xcrun notarytool store-credentials "AC_PASSWORD" \
    --apple-id "alexeymohr@gmail.com" \
    --team-id "ABCDE12345" \
    --password "abcd-efgh-ijkl-mnop"
```

Replace `alexeymohr@gmail.com`, `ABCDE12345`, and the app-specific
password with your values. The profile name `AC_PASSWORD` is
arbitrary — use something memorable.

## Local notarized build

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (ABCDE12345)"
export NOTARIZE=1
export NOTARYTOOL_PROFILE="AC_PASSWORD"
bash packaging/macos/build.sh
```

The build runs as before, plus **two notarization round-trips**:
1. **The .app is notarized + stapled first.** The .app is zipped via
   `ditto` (notarytool requires a single-file submission), uploaded
   for notarization, and on Acceptance the ticket is stapled directly
   onto the .app bundle.
2. **The DMG is built around the stapled .app**, signed, then itself
   notarized + stapled.

Both round-trips use the same submit-then-poll-`log` pattern (no
`--wait` — see below). Total time: typically 3-6 minutes for both.

**Why both .app AND DMG must be stapled:** stapling a DMG only
embeds the notarization ticket in the DMG itself, *not* in files
inside it. When users mount the DMG and drag the .app to
`/Applications`, the resulting .app has no embedded ticket. macOS
Gatekeeper falls back to an *online* lookup of the ticket via
Apple's notary service — but on macOS 15 (Sequoia) that lookup is
unreliable and routinely fails on fresh installs. The user sees
"Apple could not verify..." even though the .app was correctly
notarized. Stapling the .app itself before packaging means the
ticket travels with it everywhere and never needs an online check.

**Why we don't use `--wait`:** Apple's submission `status` endpoint
has an intermittent bug where it stalls reporting `"In Progress"` for
hours (sometimes >12h) after the submission has actually completed on
their side. The `log` endpoint always returns truth — it returns
`Submission log is not yet available...` while genuinely processing,
then returns the full JSON log the moment the submission finishes.
The build script polls `log` directly so a stalled `status` endpoint
doesn't hang the build.

The resulting `.dmg` opens on any Mac without a Gatekeeper prompt,
and the .app inside opens without a Gatekeeper prompt even when
copied to a different machine offline.

## CI builds

For GitHub Actions or similar CI:

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (ABCDE12345)"
export NOTARIZE=1
export APPLE_ID="alexeymohr@gmail.com"
export APPLE_TEAM_ID="ABCDE12345"
export APPLE_APP_PASSWORD="abcd-efgh-ijkl-mnop"
bash packaging/macos/build.sh
```

(Set these via repository Secrets, not workflow YAML.) The build
script falls back to env vars when `NOTARYTOOL_PROFILE` isn't set.

The certificate itself needs to be available to the runner — typically
via a base64-encoded `.p12` stored in a Secret, decoded into a
temporary keychain at job start. That part is workflow-specific and
out of scope for this doc.

## Local iteration without notarization

The default (no `NOTARIZE` env var) is unchanged from before:
- With `CODESIGN_IDENTITY` set: Developer-ID signed `.dmg` (Gatekeeper
  warning on first launch, but `Open Anyway` works).
- Without `CODESIGN_IDENTITY`: ad-hoc signed `.dmg` (also works after
  `Open Anyway`).

Skipping notarization is the right call for fast iter cycles —
notarization is a 1-minute round trip per build.

## Troubleshooting

**"Notarization failed: invalid"** — usually means hardened-runtime
isn't enabled on the bundle, or entitlements are wrong. Check:
- `codesign -d --verbose=4 "dist/AAF Browser.app"` — should show
  `Runtime` flag and reference `entitlements.plist`.
- `packaging/macos/entitlements.plist` should NOT include
  `com.apple.security.cs.allow-unsigned-executable-memory` etc.
  unless you actually need them (we don't).

**"Couldn't find profile AC_PASSWORD"** — the profile is per-user
keychain. If you set it as one user and run the build as another
(or in a CI environment), the profile won't be found. Re-run
`store-credentials` in the build environment, or pass env vars.

**"You must first sign in with your Apple ID"** — your credential
profile is stale (old session). Re-run `store-credentials` to
refresh.

**Apple notary service is slow** — 30-90 seconds is normal.
Multi-minute hangs occasionally happen during Apple's busy times.
The build script polls every 30s and gives up after 30 minutes.

**Build script timed out / suspect Apple's status is stuck** — query
the `log` endpoint directly:
```bash
xcrun notarytool log <SUBMISSION_ID> --keychain-profile AC_PASSWORD
```
If the response is JSON with `"status": "Accepted"`, the submission
actually succeeded (Apple's status endpoint was lying). Just staple
the existing DMG and you're done:
```bash
xcrun stapler staple "dist/AAF-Browser-vX.Y.Z-arm64.dmg"
xcrun stapler validate "dist/AAF-Browser-vX.Y.Z-arm64.dmg"
```
If it returns `"Submission log is not yet available..."`, the
submission is still genuinely processing — wait and re-query.
