#!/usr/bin/env bash
set -euo pipefail

# build_linux_docker.sh - builds the Linux AppImage in Docker, from any OS with Docker running (a Mac included).
#
#   scripts/build_linux_docker.sh
#
# The output lands in build/linux/ (not tracked): Dubcrate-linux-<arch>.AppImage, its .zip and smoke.txt, the report of opening
# the built app once (it is checked before the script succeeds). On an Apple Silicon Mac the container is Arm Linux, so the result
# is an aarch64 AppImage; the GitHub runner makes the x86_64 one. The first run builds the image and compiles ffmpeg and the app (a
# long while: Nuitka compiles all of Dubcrate); ffmpeg's build is kept in the Docker volume dubcrate-linux-build-cache, so later
# runs skip it (`docker volume rm dubcrate-linux-build-cache` builds it again). Your files are copied in read-only: nothing in the
# repository changes.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT_DIR/build/linux"
mkdir -p "$OUT_DIR"
docker build -q -f "$ROOT_DIR/scripts/linux-build.Dockerfile" --build-context repo="$ROOT_DIR" -t dubcrate-linux-build "$ROOT_DIR/scripts" >/dev/null
docker run --rm -v "$ROOT_DIR":/host:ro -v "$OUT_DIR":/out -v dubcrate-linux-build-cache:/work/build dubcrate-linux-build bash -c '
set -e
git config --global --add safe.directory "*"
rsync -a --exclude .venv --exclude /build --exclude "*.app" --exclude "*.dmg" --exclude "*.dist" --exclude "*.build" \
    --exclude src/deployment --exclude "*.spec" --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache \
    --exclude "*.AppImage" /host/ /work/
cd /work
scripts/build_linux.sh
QT_QPA_PLATFORM=offscreen src/Dubcrate.dist/main.bin --smoke-test=/out/smoke.txt || { cat /out/smoke.txt; exit 1; }
cat /out/smoke.txt
# and the AppImage itself, which has to start the same app
QT_QPA_PLATFORM=offscreen ./Dubcrate-linux-*.AppImage --appimage-extract-and-run --smoke-test=/out/smoke-appimage.txt \
    || { cat /out/smoke-appimage.txt; exit 1; }
cp Dubcrate-linux-* /out/
ls -lh /out
'
