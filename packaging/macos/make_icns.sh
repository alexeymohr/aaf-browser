#!/usr/bin/env bash
# Convert packaging/macos/icon.png (1024x1024) into icon.icns by
# building an .iconset directory at the standard Apple sizes and
# running iconutil. Uses macOS-only built-in tools (sips, iconutil),
# no extra dependencies required.
#
# Usage: bash packaging/macos/make_icns.sh
# Output: packaging/macos/icon.icns

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f icon.png ]]; then
  echo "icon.png missing; run packaging/macos/generate_icon.py first" >&2
  exit 1
fi

iconset_dir="icon.iconset"
rm -rf "$iconset_dir"
mkdir -p "$iconset_dir"

# Apple-required sizes for an .icns. Each entry is "filename pixels".
sizes=(
  "icon_16x16.png 16"
  "icon_16x16@2x.png 32"
  "icon_32x32.png 32"
  "icon_32x32@2x.png 64"
  "icon_128x128.png 128"
  "icon_128x128@2x.png 256"
  "icon_256x256.png 256"
  "icon_256x256@2x.png 512"
  "icon_512x512.png 512"
  "icon_512x512@2x.png 1024"
)

for entry in "${sizes[@]}"; do
  filename="${entry%% *}"
  pixels="${entry##* }"
  sips -z "$pixels" "$pixels" icon.png --out "$iconset_dir/$filename" \
    > /dev/null
done

iconutil -c icns -o icon.icns "$iconset_dir"
echo "wrote $(pwd)/icon.icns"
