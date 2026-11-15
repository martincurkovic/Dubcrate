#!/usr/bin/env bash
set -e

# build_linux.sh - builds the Dubcrate standalone Linux app and the AppImage that carries it
# Can be run from anywhere. Handles uv sync and the packaging (pyside6-deploy / Nuitka), the same way as JV Studio and AKAISDS.
# Needs, once:  sudo apt install build-essential patchelf curl xz-utils nasm zip file zlib1g-dev      (Nuitka compiles C; nasm and zlib are for ffmpeg; appimagetool runs `file`)
# To build in Docker from any computer (a Mac included), use scripts/build_linux_docker.sh.
#
# The app bundles LGPL builds of ffmpeg and ffprobe (scripts/build_ffmpeg_linux.sh makes them for this machine's architecture, the
# first time), which dubcrate/tools.py looks for before PATH. cdrdao, which burns off a Mac, is GPL and is not bundled: the user
# installs it (`sudo apt install cdrdao`). Output, in the repository root: Dubcrate-linux-<arch>.AppImage and a .zip of it (a zip
# keeps the executable bit when it is downloaded from a CI run).

APP_NAME="Dubcrate"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"
VERSION_FILE="$SRC_DIR/dubcrate/_build_version.py"

# appimagetool, pinned like the ffmpeg sources: change the version and the hashes together
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_SHA256_X86_64="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
APPIMAGETOOL_SHA256_AARCH64="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158"

if ! command -v gcc >/dev/null 2>&1 && ! command -v cc >/dev/null 2>&1; then
    echo "No C compiler found - install one first (Nuitka needs it):"
    echo "  sudo apt update && sudo apt install build-essential patchelf curl xz-utils nasm zip file zlib1g-dev"
    exit 1
fi

ARCH="$(uname -m)"
case "$ARCH" in
x86_64) APPIMAGETOOL_SHA256="$APPIMAGETOOL_SHA256_X86_64" ;;
aarch64) APPIMAGETOOL_SHA256="$APPIMAGETOOL_SHA256_AARCH64" ;;
*)
    echo "No AppImage tool for $ARCH (x86_64 and aarch64 only)"
    exit 1
    ;;
esac

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
uv sync
uv pip install pip

# ffmpeg and ffprobe for this architecture (a no-op if already built: delete build/ffmpeg to build them again). They are copied
# to a fixed name so the data argument below doesn't depend on the architecture.
"$ROOT_DIR/scripts/build_ffmpeg_linux.sh"
rm -rf "$ROOT_DIR/build/ffmpeg/bundle"
cp -R "$ROOT_DIR/build/ffmpeg/$ARCH" "$ROOT_DIR/build/ffmpeg/bundle"

cd "$SRC_DIR"

# derive version from nearest git tag - falls back to dev placeholder if no tags exist yet
RAW_TAG=$(git -C "$ROOT_DIR" describe --tags --abbrev=0 2>/dev/null || true)
if [ -z "$RAW_TAG" ]; then
    VERSION="0.0.0-dev"
else
    VERSION=$(echo "$RAW_TAG" | sed 's/^v//')
fi
echo "Building version: $VERSION"
# the built app shows this on its splash screen. It is only needed while compiling, and a run from source
# should say "development", so it is removed again when the script ends
echo "APP_VERSION = \"$VERSION\"" >"$VERSION_FILE"
trap 'rm -f "$VERSION_FILE"' EXIT

if [ ! -f "pysidedeploy.spec" ]; then
    echo "No spec file found - generating from defaults"

    uv run pyside6-deploy --init main.py

    sed -i "s/^title = .*/title = $APP_NAME/" pysidedeploy.spec
    sed -i "s|^icon = .*|icon = ../assets/icon/Dubcrate-512.png|" pysidedeploy.spec
    sed -i 's/^mode = .*/mode = standalone/' pysidedeploy.spec

    echo "Spec generated and configured."
fi

# the spec keeps the interpreter it was generated with, so point it at this project's environment on every run
sed -i "s|^python_path = .*|python_path = $ROOT_DIR/.venv/bin/python|" pysidedeploy.spec

# Data the app reads at run time, paths relative to src/ (where this runs), as SRC=DEST inside the app: the same as
# scripts/build_macos.sh (tests/test_build_files.py checks that they agree, and that every file exists).
DATA_ARGS="--include-data-dir=dubcrate/ui/icons=dubcrate/ui/icons --include-data-files=dubcrate/ui/style.qss.template=dubcrate/ui/style.qss.template --include-data-files=dubcrate/ui/style_system6.qss.template=dubcrate/ui/style_system6.qss.template --include-data-dir=dubcrate/ui/fonts=dubcrate/ui/fonts --include-data-dir=dubcrate/ui/help=dubcrate/ui/help --include-data-files=../assets/icon/AppIcon.png=dubcrate/ui/AppIcon.png --include-data-files=../LICENSE=LICENSE --include-data-dir=../build/ffmpeg/bundle=dubcrate/bin"

# Modules that are loaded by name, which Nuitka's import scan can miss: the rekordbox reader with its encrypted-database driver,
# and the C bridge miniaudio is built on (cffi). (No PyObjC: DiscRecording is macOS's.) --smoke-test (below) shows if one is missing.
MODULE_ARGS="--include-package=pyrekordbox --include-package=sqlcipher3 --include-package=sqlalchemy.dialects.sqlite --include-package=cffi --include-module=_cffi_backend"

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i "s|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads $MODULE_ARGS $DATA_ARGS|" pysidedeploy.spec

echo "Building $APP_NAME..."
uv run pyside6-deploy -c pysidedeploy.spec

DIST="$SRC_DIR/$APP_NAME.dist"
# Make sure the bundled tools can run: Nuitka copies data files, and the executable bit must survive.
for tool in ffmpeg ffprobe; do
    chmod 755 "$DIST/dubcrate/bin/$tool"
done

# The AppImage: an AppDir (the app, its .desktop file, the icon and an AppRun that starts it), made into one file by appimagetool.
# appimagetool is run with --appimage-extract-and-run so it needs no FUSE (a Docker container or a CI runner has none).
APPDIR="$ROOT_DIR/build/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "$DIST"/. "$APPDIR/usr/bin/"
cp "$ROOT_DIR/assets/linux/$APP_NAME.desktop" "$APPDIR/$APP_NAME.desktop"
cp "$ROOT_DIR/assets/icon/Dubcrate-512.png" "$APPDIR/$APP_NAME.png"
ln -s "$APP_NAME.png" "$APPDIR/.DirIcon"
printf '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/bin/main.bin" "$@"\n' >"$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

TOOL="$ROOT_DIR/build/downloads/appimagetool-$APPIMAGETOOL_VERSION-$ARCH.AppImage"
mkdir -p "$ROOT_DIR/build/downloads"
if [ ! -f "$TOOL" ]; then
    echo "Downloading appimagetool $APPIMAGETOOL_VERSION"
    curl -fsSL --retry 3 -o "$TOOL.part" \
        "https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/appimagetool-$ARCH.AppImage"
    mv "$TOOL.part" "$TOOL"
fi
echo "$APPIMAGETOOL_SHA256  $TOOL" | sha256sum -c - >/dev/null || {
    echo "Checksum mismatch for appimagetool: refusing to run it" >&2
    rm -f "$TOOL"
    exit 1
}
chmod +x "$TOOL"

IMAGE="$APP_NAME-linux-$ARCH.AppImage"
rm -f "$ROOT_DIR/$IMAGE" "$ROOT_DIR/$APP_NAME-linux-$ARCH.zip"
echo "Creating $IMAGE..."
ARCH="$ARCH" "$TOOL" --appimage-extract-and-run "$APPDIR" "$ROOT_DIR/$IMAGE"
(cd "$ROOT_DIR" && zip -q "$APP_NAME-linux-$ARCH.zip" "$IMAGE")

echo ""
echo "Done - the app is $DIST (run main.bin in it)."
echo "AppImage: $ROOT_DIR/$IMAGE (and $APP_NAME-linux-$ARCH.zip)"
