#!/usr/bin/env bash
set -e

# make_icons.sh - makes the macOS app icon from the master, assets/icon/AppIcon.png:
#   assets/icon/Dubcrate-macos.icns    all ten sizes (16, 32, 128, 256 and 512 points, each at 1x and 2x)
#
# The master is exactly 1024x1024 and already in macOS icon form (rounded square, transparent corners), as exported by
# icon.kitchen (see AppIcon.txt). Every size is scaled DOWN from it, never up, which is why it has to be the 1024px one.
# Needs macOS (sips and iconutil ship with it). scripts/build_macos.sh runs this on every build; the .icns is not tracked.
#
#   scripts/make_icons.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICON_DIR="$ROOT_DIR/assets/icon"
MASTER="$ICON_DIR/AppIcon.png"

[ -f "$MASTER" ] || { echo "Missing $MASTER - the 1024x1024 macOS-style app icon."; exit 1; }
WIDTH=$(sips -g pixelWidth "$MASTER" | awk '/pixelWidth/ {print $2}')
HEIGHT=$(sips -g pixelHeight "$MASTER" | awk '/pixelHeight/ {print $2}')
if [ "$WIDTH" != "1024" ] || [ "$HEIGHT" != "1024" ]; then
    echo "AppIcon.png must be exactly 1024x1024 (it is ${WIDTH}x${HEIGHT})"
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
ICONSET="$WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for POINTS in 16 32 128 256 512; do
    sips -z $POINTS $POINTS "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}.png" >/dev/null
    DOUBLE=$((POINTS * 2))
    if [ "$DOUBLE" = "1024" ]; then
        cp "$MASTER" "$ICONSET/icon_${POINTS}x${POINTS}@2x.png"
    else
        sips -z $DOUBLE $DOUBLE "$MASTER" --out "$ICONSET/icon_${POINTS}x${POINTS}@2x.png" >/dev/null
    fi
done
iconutil -c icns "$ICONSET" -o "$ICON_DIR/Dubcrate-macos.icns"
echo "Wrote $ICON_DIR/Dubcrate-macos.icns"
