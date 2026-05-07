#!/usr/bin/env bash
# End-to-end local build for the macOS .app + .dmg.
#
# Usage:
#   bash packaging/macos/build.sh
#
# Output:
#   dist/AAF Browser.app                — the .app bundle (signed)
#   dist/AAF-Browser-vVERSION-arm64.dmg — the distributable .dmg (signed)
#
# Required tools:
#   - python with `aafbrowser[mac-build]` installed
#       (pip install -e '.[mac-build]')
#   - create-dmg
#       (brew install create-dmg)
#
# Codesigning:
#   - If $CODESIGN_IDENTITY is set, signs with that identity using the
#     hardened runtime + the entitlements file. Required for
#     notarization.
#   - Otherwise signs ad-hoc (`-`). Local dev iteration works; users
#     downloading an ad-hoc-signed .dmg get a Gatekeeper warning on
#     first launch and need to use System Settings -> Privacy &
#     Security -> "Open Anyway".

set -euo pipefail

# -- locate repo + read version --------------------------------------
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
cd "$REPO_ROOT"

VERSION="$(grep -m1 '^version = ' pyproject.toml | sed -E 's/^version = "(.+)"/\1/')"
if [[ -z "$VERSION" ]]; then
  echo "could not read version from pyproject.toml" >&2
  exit 1
fi
echo "==> AAF Browser v${VERSION}"

# -- preflight -------------------------------------------------------
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller not importable. Install via:" >&2
  echo "    pip install -e '.[mac-build]'" >&2
  exit 1
fi
if ! command -v create-dmg >/dev/null 2>&1; then
  echo "create-dmg not on PATH. Install via:" >&2
  echo "    brew install create-dmg" >&2
  exit 1
fi

# -- icon ------------------------------------------------------------
if [[ ! -f packaging/macos/icon.icns ]]; then
  echo "==> Building icon.icns from icon.png"
  bash packaging/macos/make_icns.sh
fi

# -- pyinstaller -----------------------------------------------------
echo "==> Running PyInstaller"
"$PYTHON" -m PyInstaller --noconfirm --clean \
  packaging/macos/aafbrowser.spec

APP="dist/AAF Browser.app"
if [[ ! -d "$APP" ]]; then
  echo "PyInstaller did not produce $APP" >&2
  exit 1
fi

# -- code signing ----------------------------------------------------
ENTITLEMENTS="packaging/macos/entitlements.plist"
SIGN_ARGS=(--deep --force --options runtime --timestamp)
if [[ -f "$ENTITLEMENTS" ]]; then
  SIGN_ARGS+=(--entitlements "$ENTITLEMENTS")
fi

if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "==> Signing with Developer ID: $CODESIGN_IDENTITY"
  codesign --sign "$CODESIGN_IDENTITY" "${SIGN_ARGS[@]}" "$APP"
else
  echo "==> Signing ad-hoc (CODESIGN_IDENTITY not set)"
  # Hardened runtime can't be combined with ad-hoc on some macOS
  # versions; drop the runtime flag for local-only builds.
  codesign --sign - --deep --force "$APP"
fi

# Verify the signature
codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -5 || true

# -- notarization setup (opt-in) ------------------------------------
# Gated on NOTARIZE=1. Two credential paths:
#   - NOTARYTOOL_PROFILE=<name>: stored credential profile via
#     `xcrun notarytool store-credentials`. Recommended for local
#     iter (one-time setup, no env vars needed thereafter).
#   - APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD env vars: passed
#     each time. Suited for CI where secrets are injected at build.
# Without NOTARIZE=1, this section is inert and local builds stay
# fast.
CRED_ARGS=()
if [[ "${NOTARIZE:-0}" == "1" ]]; then
  if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
    echo "==> NOTARIZE=1 set but CODESIGN_IDENTITY is empty" >&2
    echo "    Notarization requires a Developer ID-signed bundle." >&2
    exit 1
  fi
  if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
    CRED_ARGS=(--keychain-profile "$NOTARYTOOL_PROFILE")
  else
    : "${APPLE_ID:?APPLE_ID env var required when NOTARYTOOL_PROFILE is unset}"
    : "${APPLE_TEAM_ID:?APPLE_TEAM_ID env var required}"
    : "${APPLE_APP_PASSWORD:?APPLE_APP_PASSWORD env var required}"
    CRED_ARGS=(
      --apple-id "$APPLE_ID"
      --team-id "$APPLE_TEAM_ID"
      --password "$APPLE_APP_PASSWORD"
    )
  fi
fi

# notarize_and_staple SUBMIT_PATH STAPLE_PATH
#
# Submits SUBMIT_PATH to Apple's notary service, polls the `log`
# endpoint until the submission resolves, then staples STAPLE_PATH.
# SUBMIT_PATH and STAPLE_PATH may differ (you submit a .zip of an
# .app but staple the .app itself).
#
# We deliberately do NOT use `notarytool submit --wait`: Apple's
# status endpoint has an intermittent bug where it stalls reporting
# "In Progress" for hours (sometimes >12h) after the submission has
# actually completed. The `log` endpoint returns truth even when
# status is stuck.
notarize_and_staple() {
  local SUBMIT_PATH="$1"
  local STAPLE_PATH="$2"

  echo "  Submitting: $SUBMIT_PATH"
  local SUBMIT_OUTPUT
  SUBMIT_OUTPUT=$(xcrun notarytool submit "$SUBMIT_PATH" "${CRED_ARGS[@]}" 2>&1)
  echo "$SUBMIT_OUTPUT" | sed 's/^/    /'
  local SUB_ID
  SUB_ID=$(echo "$SUBMIT_OUTPUT" | awk '/^[[:space:]]+id:/ {print $2; exit}')
  if [[ -z "$SUB_ID" ]]; then
    echo "  Could not extract submission ID from notarytool output" >&2
    return 1
  fi
  echo "  Submission ID: $SUB_ID"

  echo "  Polling notary log endpoint (typical: 1-3 min, max: 30 min)"
  local POLL_TIMEOUT=1800
  local POLL_INTERVAL=30
  local ELAPSED=0
  local LOG_JSON=""
  local LAST_OUTPUT=""
  while (( ELAPSED < POLL_TIMEOUT )); do
    LAST_OUTPUT=$(xcrun notarytool log "$SUB_ID" "${CRED_ARGS[@]}" 2>&1 || true)
    if echo "$LAST_OUTPUT" | grep -q '"status"'; then
      LOG_JSON="$LAST_OUTPUT"
      break
    fi
    sleep "$POLL_INTERVAL"
    ELAPSED=$((ELAPSED + POLL_INTERVAL))
    echo "    (${ELAPSED}s elapsed, still waiting...)"
  done

  if [[ -z "$LOG_JSON" ]]; then
    echo "  Notarization timed out after ${POLL_TIMEOUT}s." >&2
    echo "    Submission ID: $SUB_ID" >&2
    echo "    Last log response:" >&2
    echo "$LAST_OUTPUT" | sed 's/^/      /' >&2
    return 1
  fi

  if ! echo "$LOG_JSON" | grep -q '"status": "Accepted"'; then
    echo "  Notarization rejected. Full log:" >&2
    echo "$LOG_JSON" >&2
    return 1
  fi
  echo "  Notarization Accepted"

  echo "  Stapling: $STAPLE_PATH"
  xcrun stapler staple "$STAPLE_PATH"
  xcrun stapler validate "$STAPLE_PATH"
}

# -- notarize the .app (opt-in) -------------------------------------
# This MUST happen before building the DMG. The .app needs to carry
# its own offline notarization ticket because:
#  1. Stapling a DMG only puts the ticket on the DMG, not on files
#     inside it.
#  2. When users drag the .app out of the DMG to /Applications, the
#     .app no longer benefits from the DMG's ticket.
#  3. macOS Gatekeeper falls back to an online ticket lookup via
#     Apple's notary service, but on macOS 15 (Sequoia) that lookup
#     is unreliable and routinely fails on fresh installs — yielding
#     "Apple could not verify..." even on a properly notarized .app.
#  4. Fix: staple the .app itself, then build the DMG around the
#     already-stapled .app. The .app then opens reliably even
#     offline.
if [[ "${NOTARIZE:-0}" == "1" ]]; then
  echo "==> Notarizing .app"
  APP_ZIP="dist/AAF-Browser-v${VERSION}-app-for-notary.zip"
  rm -f "$APP_ZIP"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$APP_ZIP"
  notarize_and_staple "$APP_ZIP" "$APP"
  rm -f "$APP_ZIP"
fi

# -- DMG -------------------------------------------------------------
DMG_NAME="AAF-Browser-v${VERSION}-arm64.dmg"
DMG_PATH="dist/${DMG_NAME}"
echo "==> Building $DMG_NAME"
rm -f "$DMG_PATH"

# create-dmg's flags: small window, drag-to-Applications symlink,
# .app placed in the left half. Background image is intentionally
# omitted for v1 (placeholder design).
create-dmg \
  --volname "AAF Browser ${VERSION}" \
  --window-size 540 360 \
  --icon-size 96 \
  --icon "AAF Browser.app" 140 180 \
  --app-drop-link 400 180 \
  --no-internet-enable \
  "$DMG_PATH" \
  "$APP" \
  > /dev/null

# Sign the DMG too if we have a real identity (notarization expects
# the dmg to be signed by the same identity as the .app).
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "==> Signing DMG"
  codesign --sign "$CODESIGN_IDENTITY" --timestamp "$DMG_PATH"
fi

# -- notarize the DMG (opt-in) --------------------------------------
# The DMG is notarized + stapled too so that mounting it doesn't
# trigger a Gatekeeper warning on the DMG itself.
if [[ "${NOTARIZE:-0}" == "1" ]]; then
  echo "==> Notarizing DMG"
  notarize_and_staple "$DMG_PATH" "$DMG_PATH"
fi

echo
echo "Build complete:"
echo "  $APP"
echo "  $DMG_PATH"
ls -lh "$APP" | head -1
ls -lh "$DMG_PATH"
