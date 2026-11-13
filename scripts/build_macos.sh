#!/usr/bin/env bash
set -e

# build_macos.sh - builds the Dubcrate.app bundle and the DMG that installs it
# Can be run from anywhere. Handles uv sync and the packaging (pyside6-deploy / Nuitka), the same way as AKAISDS and JV Studio.
#
# The app is built for macOS 14 and later, the oldest the Qt 6.12 wheels support. It bundles LGPL builds of ffmpeg and ffprobe
# (scripts/build_ffmpeg_macos.sh makes them for this Mac's architecture, the first time), which dubcrate/tools.py looks for
# before PATH.

APP_NAME="Dubcrate"
MIN_MACOS="14.0"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"
VERSION_FILE="$SRC_DIR/dubcrate/_build_version.py"

cd "$ROOT_DIR"
echo "Syncing environment with uv..."
uv sync
uv pip install pip

# build for the oldest macOS the app supports rather than for whatever the build machine runs
# (which would make the app refuse to start on anything older)
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"

# the .icns is made from the 1024px master (assets/icon/AppIcon.png) on every build; it is not tracked
"$ROOT_DIR/scripts/make_icons.sh"

# ffmpeg and ffprobe for this architecture (a no-op if already built: delete build/ffmpeg to build them again). They are copied
# to a fixed name so the data argument below doesn't depend on the architecture.
"$ROOT_DIR/scripts/build_ffmpeg_macos.sh"
rm -rf "$ROOT_DIR/build/ffmpeg/bundle"
cp -R "$ROOT_DIR/build/ffmpeg/$(uname -m)" "$ROOT_DIR/build/ffmpeg/bundle"

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

    sed -i '' "s/^title = .*/title = $APP_NAME/" pysidedeploy.spec
    sed -i '' "s|^icon = .*|icon = ../assets/icon/Dubcrate-macos.icns|" pysidedeploy.spec
    sed -i '' 's/^mode = .*/mode = standalone/' pysidedeploy.spec

    echo "Spec generated and configured."
fi

# the spec keeps the interpreter it was generated with, so point it at this project's environment on every run
sed -i '' "s|^python_path = .*|python_path = $ROOT_DIR/.venv/bin/python|" pysidedeploy.spec

# Data the app reads at run time, paths relative to src/ (where this runs), as SRC=DEST inside the app:
#   the stylesheet templates, icons and the System 6 font, the user guide, the app icon (the splash and the window icon;
#   dubcrate/ui/app.py looks for it beside itself), the licence, and ffmpeg and ffprobe with their licences (as dubcrate/bin, where
#   dubcrate/tools.py looks). tests/test_build_files.py checks that these exist (the ffmpeg folder is made above, so not in a clean checkout).
DATA_ARGS="--include-data-dir=dubcrate/ui/icons=dubcrate/ui/icons --include-data-files=dubcrate/ui/style.qss.template=dubcrate/ui/style.qss.template --include-data-files=dubcrate/ui/style_system6.qss.template=dubcrate/ui/style_system6.qss.template --include-data-dir=dubcrate/ui/fonts=dubcrate/ui/fonts --include-data-dir=dubcrate/ui/help=dubcrate/ui/help --include-data-files=../assets/icon/AppIcon.png=dubcrate/ui/AppIcon.png --include-data-files=../LICENSE=LICENSE --include-data-dir=../build/ffmpeg/bundle=dubcrate/bin"

# Modules that are loaded by name, which Nuitka's import scan can miss: the bridge to Apple's DiscRecording framework
# (PyObjC), the rekordbox reader with its encrypted-database driver, and the C bridge miniaudio is built on (cffi). --smoke-test (below) shows if one is still missing.
MODULE_ARGS="--include-package=objc --include-package=DiscRecording --include-package=Foundation --include-package=pyrekordbox --include-package=sqlcipher3 --include-package=sqlalchemy.dialects.sqlite --include-package=cffi --include-module=_cffi_backend"

# re-generate extra_args on EVERY run, that way version number can update correctly
sed -i '' "s|^extra_args = .*|extra_args = --quiet --noinclude-qt-translations --assume-yes-for-downloads --macos-app-name=$APP_NAME --macos-app-version=$VERSION $MODULE_ARGS $DATA_ARGS|" pysidedeploy.spec

echo "Building $APP_NAME.app..."
uv run pyside6-deploy -c pysidedeploy.spec

# Make sure the bundled tools can run (Nuitka copies data files, and the executable bit must survive), and sign them: a macOS
# binary that is modified or unsigned won't start on Apple Silicon.
BUNDLED_TOOLS="$APP_NAME.app/Contents/MacOS/dubcrate/bin"
for tool in ffmpeg ffprobe; do
    chmod 755 "$BUNDLED_TOOLS/$tool"
    /usr/bin/codesign -s - --force "$BUNDLED_TOOLS/$tool"
done

# Nuitka has no option for a minimum macOS version, so set it in Info.plist: on anything older macOS then says the app can't be
# opened with this version instead of letting it crash. Nuitka's ad-hoc signature seals Info.plist, so sign again the way Nuitka does.
plutil -replace LSMinimumSystemVersion -string "$MIN_MACOS" "$APP_NAME.app/Contents/Info.plist"
/usr/bin/codesign -s - --force --deep --preserve-metadata=entitlements "$APP_NAME.app"

# The DMG: dmgbuild (a build dependency, pure Python) writes the window layout directly - no Finder/AppleScript, so it works on a
# headless CI runner. It opens as a window with the app, an Applications shortcut and a background image with an arrow and
# "Drag Dubcrate to the Applications folder". Layout: scripts/dmg_settings.py; background: assets/dmg/dmg_background.tiff
# (regenerate with assets/dmg/create_dmg_background.py). The name is what the workflows upload.
case "$(uname -m)" in
arm64) DMG_ARCH="arm" ;;
*) DMG_ARCH="intel" ;;
esac
DMG_NAME="$APP_NAME-macos-$DMG_ARCH.dmg"
echo "Creating $DMG_NAME..."
rm -f "$ROOT_DIR/$DMG_NAME"
uv run dmgbuild \
    -s "$ROOT_DIR/scripts/dmg_settings.py" \
    -D app="$APP_NAME.app" \
    -D background="$ROOT_DIR/assets/dmg/dmg_background.tiff" \
    "$APP_NAME" "$ROOT_DIR/$DMG_NAME"

echo ""
echo "Done - check the output above for exactly where the .app landed."
echo "DMG: $ROOT_DIR/$DMG_NAME"
