# PyInstaller spec for the AAF Browser macOS .app bundle.
#
# Run from the repo root:
#     pyinstaller packaging/macos/aafbrowser.spec
#
# Output: dist/AAF Browser.app  (--onedir form, --windowed).
# Codesigning + .dmg packaging happen in packaging/macos/build.sh
# after PyInstaller produces the bundle.
#
# Key gotchas:
# - pyaaf2 uses dynamic class registration; collect_submodules('aaf2')
#   force-includes everything PyInstaller's static analyzer would miss.
# - The static folder is bundled as data under aafbrowser/web/static
#   so app.py's `Path(__file__).parent / "static"` resolves correctly.
# - target_arch="arm64" matches the locked-in Phase 4 decision.

import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH = directory of this spec file. Repo root is two levels up.
REPO_ROOT = Path(SPECPATH).parent.parent
ENTRY = str(REPO_ROOT / "aafbrowser" / "_app_entry.py")
STATIC_DIR = str(REPO_ROOT / "aafbrowser" / "web" / "static")

with open(REPO_ROOT / "pyproject.toml", "rb") as _pp:
    APP_VERSION = tomllib.load(_pp)["project"]["version"]

# Defensive against pyaaf2's dynamic AAF class registration —
# without this, several aaf2.* submodules don't make it into the
# bundle and the app crashes on first read of a real AAF.
HIDDEN_IMPORTS = collect_submodules("aaf2")

ICON_PATH = REPO_ROOT / "packaging" / "macos" / "icon.icns"
ICON_KW = {"icon": str(ICON_PATH)} if ICON_PATH.is_file() else {}


a = Analysis(
    [ENTRY],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[(STATIC_DIR, "aafbrowser/web/static")],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AAF Browser",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # --windowed
    disable_windowed_traceback=False,
    target_arch="arm64",
    codesign_identity=None,         # signed by build.sh after the fact
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AAF Browser",
)

app = BUNDLE(
    coll,
    name="AAF Browser.app",
    bundle_identifier="com.alexeymohr.aafbrowser",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "AAF Browser",
        "CFBundleDisplayName": "AAF Browser",
        "CFBundleVersion": APP_VERSION,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleIdentifier": "com.alexeymohr.aafbrowser",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Advanced Authoring Format File",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["com.alexeymohr.aafbrowser.aaf"],
            },
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": "com.alexeymohr.aafbrowser.aaf",
                "UTTypeDescription": "Advanced Authoring Format File",
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {
                    "public.filename-extension": ["aaf"],
                },
            },
        ],
    },
    **ICON_KW,
)
