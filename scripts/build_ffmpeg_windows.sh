#!/usr/bin/env bash
set -euo pipefail

# build_ffmpeg_windows.sh - builds the ffmpeg.exe and ffprobe.exe that the Windows installer bundles (64-bit Windows).
#
# Run it from an MSYS2 "MINGW64" shell (scripts/build_windows.ps1 does that for you). It is the same build as
# scripts/build_ffmpeg_macos.sh and scripts/build_ffmpeg_linux.sh: the same pinned FFmpeg and LAME sources, LGPL (no
# --enable-gpl, no --enable-nonfree), static, with the LAME MP3 encoder (LGPL) linked in, and only the decoders, filters, encoders
# and muxers Dubcrate uses (tests/test_build_files.py checks that the three agree). Static means the programs need nothing
# but the DLLs every Windows has (the C runtime, kernel32 and friends): no libgcc, no winpthreads, no zlib DLL to ship.
#
# Output (build/ffmpeg/x86_64/, not tracked): ffmpeg.exe, ffprobe.exe, and licenses/ (the licence texts, the build configuration
# and the written offer of source). scripts/build_windows.ps1 calls this when that folder is missing and bundles it as dubcrate/bin.
#
#   scripts/build_ffmpeg_windows.sh            build if needed
#   scripts/build_ffmpeg_windows.sh --force    build again
#
# Needs, in MSYS2 (https://www.msys2.org): pacman -S --needed base-devel diffutils make nasm xz curl
#     mingw-w64-x86_64-gcc mingw-w64-x86_64-zlib mingw-w64-x86_64-pkgconf
# Downloads are checked against the SHA-256 below; to move to a new version, change the version and the hash together
# (in all three ffmpeg scripts, and in src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md).
# NOT YET RUN: written from the Linux script and MSYS2's documentation; the first run is the first CI run.

FFMPEG_VERSION="9.0.2"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
FFMPEG_SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"

LAME_VERSION="3.100"
LAME_URL="https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="x86_64"
WORK="$ROOT_DIR/build/ffmpeg-work-windows"
OUT="$ROOT_DIR/build/ffmpeg/$ARCH"
JOBS="$(nproc)"

if [ "${MSYSTEM:-}" != "MINGW64" ]; then
    echo "Run this from an MSYS2 MINGW64 shell (MSYSTEM is '${MSYSTEM:-unset}')." >&2
    exit 1
fi

if [ "${1:-}" != "--force" ] && [ -x "$OUT/ffmpeg.exe" ] && [ -x "$OUT/ffprobe.exe" ]; then
    echo "ffmpeg and ffprobe are already built: $OUT (use --force to build again)"
    exit 0
fi

fetch() { # fetch URL SHA256 FILE
    if [ ! -f "$3" ]; then
        echo "Downloading $1"
        curl -fsSL --retry 3 -o "$3.part" "$1"
        mv "$3.part" "$3"
    fi
    echo "$2  $3" | sha256sum -c - >/dev/null || {
        echo "Checksum mismatch for $3: refusing to build from it" >&2
        rm -f "$3"
        exit 1
    }
}

rm -rf "$WORK" "$OUT"
mkdir -p "$WORK/src" "$WORK/prefix" "$OUT/licenses"
DOWNLOADS="$ROOT_DIR/build/downloads"
mkdir -p "$DOWNLOADS"

fetch "$LAME_URL" "$LAME_SHA256" "$DOWNLOADS/lame-$LAME_VERSION.tar.gz"
fetch "$FFMPEG_URL" "$FFMPEG_SHA256" "$DOWNLOADS/ffmpeg-$FFMPEG_VERSION.tar.xz"
tar -xzf "$DOWNLOADS/lame-$LAME_VERSION.tar.gz" -C "$WORK/src"
tar -xJf "$DOWNLOADS/ffmpeg-$FFMPEG_VERSION.tar.xz" -C "$WORK/src"

echo "Building LAME $LAME_VERSION ($ARCH)..."
cd "$WORK/src/lame-$LAME_VERSION"
./configure --prefix="$WORK/prefix" --disable-shared --enable-static --disable-frontend --disable-decoder \
    --disable-dependency-tracking CFLAGS="-O2" >"$WORK/lame-configure.log" 2>&1 ||
    { tail -30 "$WORK/lame-configure.log"; exit 1; }
make -j"$JOBS" >"$WORK/lame-make.log" 2>&1 || { tail -30 "$WORK/lame-make.log"; exit 1; }
make install >>"$WORK/lame-make.log" 2>&1

echo "Building ffmpeg $FFMPEG_VERSION ($ARCH)..."
# What Dubcrate asks of ffmpeg (render.py, audio/decode.py): read any common audio file (every decoder, demuxer, parser and
# filter is kept), write 16-bit PCM to a pipe or a file, and write MP3. So only those encoders and muxers, the file and pipe
# protocols, and no devices, hardware acceleration or network. zlib is MSYS2's static one.
cd "$WORK/src/ffmpeg-$FFMPEG_VERSION"
./configure \
    --prefix="$WORK/ffmpeg-prefix" \
    --target-os=mingw32 --arch=x86_64 \
    --disable-autodetect --enable-zlib \
    --disable-shared --enable-static \
    --disable-doc --disable-debug --disable-ffplay \
    --disable-network --disable-hwaccels --disable-indevs --disable-outdevs \
    --disable-encoders --enable-encoder=pcm_s16le,libmp3lame \
    --disable-muxers --enable-muxer=pcm_s16le,wav,mp3,null \
    --disable-protocols --enable-protocol=file,pipe \
    --enable-libmp3lame \
    --pkg-config-flags=--static \
    --extra-cflags="-I$WORK/prefix/include" \
    --extra-ldflags="-L$WORK/prefix/lib -static" \
    >"$WORK/ffmpeg-configure.log" 2>&1 || { tail -40 "$WORK/ffmpeg-configure.log"; exit 1; }
make -j"$JOBS" ffmpeg.exe ffprobe.exe >"$WORK/ffmpeg-make.log" 2>&1 || { tail -40 "$WORK/ffmpeg-make.log"; exit 1; }

cp ffmpeg.exe ffprobe.exe "$OUT/"
strip --strip-unneeded "$OUT/ffmpeg.exe" "$OUT/ffprobe.exe"

# Checks: it must really be an LGPL build, and must need no DLL but the ones every Windows has (the C runtime and the system's own).
for tool in ffmpeg ffprobe; do
    "$OUT/$tool.exe" -version >/dev/null
    if "$OUT/$tool.exe" -version | grep -q -e "--enable-gpl" -e "--enable-nonfree"; then
        echo "$tool was built as GPL or nonfree: that can't be bundled" >&2
        exit 1
    fi
    if objdump -p "$OUT/$tool.exe" | awk '/DLL Name:/ {print tolower($3)}' |
        grep -v -E '^(kernel32|msvcrt|ucrtbase|user32|advapi32|shell32|ole32|oleaut32|ws2_32|bcrypt|ntdll|gdi32|shlwapi|crypt32|secur32|userenv|psapi|api-ms-win-crt-[a-z0-9-]+)\.dll$'; then
        echo "$tool needs a DLL that is not part of every Windows (listed above)" >&2
        exit 1
    fi
done
"$OUT/ffmpeg.exe" -hide_banner -encoders | grep -q libmp3lame || { echo "the MP3 encoder is missing" >&2; exit 1; }

# What goes with the binaries: the licence texts, how they were built and the offer of source (LGPL 2.1 section 6).
cp "$WORK/src/ffmpeg-$FFMPEG_VERSION/COPYING.LGPLv2.1" "$OUT/licenses/FFmpeg-COPYING.LGPLv2.1.txt"
cp "$WORK/src/lame-$LAME_VERSION/COPYING" "$OUT/licenses/LAME-COPYING.txt"
"$OUT/ffmpeg.exe" -hide_banner -buildconf >"$OUT/licenses/ffmpeg-buildconf.txt"
cat >"$OUT/licenses/README.txt" <<README
ffmpeg and ffprobe, as bundled with Dubcrate
============================================

FFmpeg $FFMPEG_VERSION (https://ffmpeg.org) and the LAME MP3 encoder $LAME_VERSION (https://lame.sourceforge.io), both under the
GNU Lesser General Public Licence (see the two licence files in this folder). FFmpeg here is built without --enable-gpl and
without --enable-nonfree; LAME is linked into it statically. Dubcrate only runs these programs; it does not link them.

Source: the exact sources are
  $FFMPEG_URL
  $LAME_URL
and the script that builds them, with every configure option, is scripts/build_ffmpeg_windows.sh in the Dubcrate source
(https://github.com/martincurkovic/Dubcrate). The configuration of these binaries is in ffmpeg-buildconf.txt. Anyone may
rebuild them from those and put their own ffmpeg.exe and ffprobe.exe in this folder, or install ffmpeg and ffprobe on their PATH
(Dubcrate uses a copy in this folder first, then PATH).
README

echo ""
echo "Done: $OUT"
ls -lh "$OUT/ffmpeg.exe" "$OUT/ffprobe.exe"
