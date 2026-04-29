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

# -- notarization (opt-in) -------------------------------------------
# Gated on NOTARIZE=1. Two credential paths:
#   - NOTARYTOOL_PROFILE=<name>: stored credential profile via
#     `xcrun notarytool store-credentials`. Recommended for local
#     iter (one-time setup, no env vars needed thereafter).
#   - APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PASSWORD env vars: passed
#     each time. Suited for CI where secrets are injected at build.
# Without NOTARIZE=1, this section is inert and local builds stay
# fast. See docs/notarization-setup.md for the one-time setup.
if [[ "${NOTARIZE:-0}" == "1" ]]; then
  if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
    echo "==> NOTARIZE=1 set but CODESIGN_IDENTITY is empty" >&2
    echo "    Notarization requires a Developer ID-signed bundle." >&2
    exit 1
  fi
  echo "==> Submitting to Apple notary service (this can take a minute)"
  if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
    xcrun notarytool submit "$DMG_PATH" \
      --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  else
    : "${APPLE_ID:?APPLE_ID env var required when NOTARYTOOL_PROFILE is unset}"
    : "${APPLE_TEAM_ID:?APPLE_TEAM_ID env var required}"
    : "${APPLE_APP_PASSWORD:?APPLE_APP_PASSWORD env var required}"
    xcrun notarytool submit "$DMG_PATH" \
      --apple-id "$APPLE_ID" \
      --team-id "$APPLE_TEAM_ID" \
      --password "$APPLE_APP_PASSWORD" \
      --wait
  fi
  echo "==> Stapling notarization ticket"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
fi

echo
echo "Build complete:"
echo "  $APP"
echo "  $DMG_PATH"
ls -lh "$APP" | head -1
ls -lh "$DMG_PATH"
