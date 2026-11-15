#!/usr/bin/env bash
set -euo pipefail

# build_ffmpeg_linux.sh - builds the ffmpeg and ffprobe that the AppImage bundles, for the Linux this runs on (x86_64 or aarch64).
#
# Why not the distribution's: Debian and Ubuntu build ffmpeg with --enable-gpl and it links dozens of shared libraries, so it can't
# be copied into an AppImage. These are LGPL builds (no --enable-gpl, no --enable-nonfree), static, with the LAME MP3 encoder (LGPL) built in: ffmpeg decodes MP3 by
# itself but has no MP3 encoder of its own, and MP3 CDs need one for any file that isn't constant bitrate. LAME is compiled
# first and linked statically, so there is no separate library to ship.
#
# Output (build/ffmpeg/<arch>/, not tracked): ffmpeg, ffprobe, and licenses/ (the licence texts, the build configuration and the
# written offer of source). scripts/build_linux.sh calls this when that folder is missing and bundles it as dubcrate/bin.
# The same versions and options as scripts/build_ffmpeg_macos.sh (tests/test_build_files.py checks that they agree).
#
#   scripts/build_ffmpeg_linux.sh            build if needed
#   scripts/build_ffmpeg_linux.sh --force    build again
#
# Needs a C compiler, make, curl, xz, zlib's headers and (on x86_64) nasm: `sudo apt install build-essential curl xz-utils zlib1g-dev nasm`. Without nasm
# ffmpeg's x86 code is much slower, so the script builds without it only with a warning. Downloads are checked against the SHA-256 below; to move to a new version, change the version and the hash together
# (and update the entry in src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md).

FFMPEG_VERSION="9.0.2"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz"
FFMPEG_SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"

LAME_VERSION="3.100"
LAME_URL="https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
WORK="$ROOT_DIR/build/ffmpeg-work-$ARCH"
OUT="$ROOT_DIR/build/ffmpeg/$ARCH"
JOBS="$(nproc)"

if [ "${1:-}" != "--force" ] && [ -x "$OUT/ffmpeg" ] && [ -x "$OUT/ffprobe" ]; then
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
    --disable-dependency-tracking CFLAGS="-O2 -fPIC" >"$WORK/lame-configure.log" 2>&1 ||
    { tail -30 "$WORK/lame-configure.log"; exit 1; }
make -j"$JOBS" >"$WORK/lame-make.log" 2>&1 || { tail -30 "$WORK/lame-make.log"; exit 1; }
make install >>"$WORK/lame-make.log" 2>&1

echo "Building ffmpeg $FFMPEG_VERSION ($ARCH)..."
# What Dubcrate asks of ffmpeg (render.py, audio/decode.py): read any common audio file (every decoder, demuxer, parser and
# filter is kept), write 16-bit PCM to a pipe or a file, and write MP3. So only those encoders and muxers, the file and pipe
# protocols, and no devices, hardware acceleration or network. zlib is the system's (libz.so.1 is on every Linux).
cd "$WORK/src/ffmpeg-$FFMPEG_VERSION"
EXTRA_CONFIGURE=()
if [ "$ARCH" = "x86_64" ] && ! command -v nasm >/dev/null 2>&1 && ! command -v yasm >/dev/null 2>&1; then
    echo "WARNING: nasm is not installed, so ffmpeg is built without its x86 assembly (slower). sudo apt install nasm" >&2
    EXTRA_CONFIGURE+=(--disable-x86asm)
fi
./configure \
    --prefix="$WORK/ffmpeg-prefix" \
    --disable-autodetect --enable-zlib \
    --disable-shared --enable-static \
    --disable-doc --disable-debug --disable-ffplay \
    --disable-network --disable-hwaccels --disable-indevs --disable-outdevs \
    --disable-encoders --enable-encoder=pcm_s16le,libmp3lame \
    --disable-muxers --enable-muxer=pcm_s16le,wav,mp3,null \
    --disable-protocols --enable-protocol=file,pipe \
    --enable-libmp3lame \
    --extra-cflags="-I$WORK/prefix/include" \
    --extra-ldflags="-L$WORK/prefix/lib" \
    "${EXTRA_CONFIGURE[@]}" \
    >"$WORK/ffmpeg-configure.log" 2>&1 || { tail -40 "$WORK/ffmpeg-configure.log"; exit 1; }
make -j"$JOBS" ffmpeg ffprobe >"$WORK/ffmpeg-make.log" 2>&1 || { tail -40 "$WORK/ffmpeg-make.log"; exit 1; }

cp ffmpeg ffprobe "$OUT/"
strip --strip-unneeded "$OUT/ffmpeg" "$OUT/ffprobe"

# Checks: it must really be an LGPL build, and must not depend on anything but the C library and zlib, which every Linux has.
for tool in ffmpeg ffprobe; do
    "$OUT/$tool" -version >/dev/null
    if "$OUT/$tool" -version | grep -q -e "--enable-gpl" -e "--enable-nonfree"; then
        echo "$tool was built as GPL or nonfree: that can't be bundled" >&2
        exit 1
    fi
    if ldd "$OUT/$tool" | awk '{print $1}' | grep -v -e '^linux-vdso' -e '^libc\.so' -e '^libm\.so' -e '^libz\.so' \
        -e '^libpthread\.so' -e '^libdl\.so' -e '^librt\.so' -e '^/lib' -e '^ld-linux'; then
        echo "$tool links a library that is not part of every Linux (listed above)" >&2
        exit 1
    fi
done
"$OUT/ffmpeg" -hide_banner -encoders | grep -q libmp3lame || { echo "the MP3 encoder is missing" >&2; exit 1; }

# What goes with the binaries: the licence texts, how they were built and the offer of source (LGPL 2.1 section 6).
cp "$WORK/src/ffmpeg-$FFMPEG_VERSION/COPYING.LGPLv2.1" "$OUT/licenses/FFmpeg-COPYING.LGPLv2.1.txt"
cp "$WORK/src/lame-$LAME_VERSION/COPYING" "$OUT/licenses/LAME-COPYING.txt"
"$OUT/ffmpeg" -hide_banner -buildconf >"$OUT/licenses/ffmpeg-buildconf.txt"
cat >"$OUT/licenses/README.txt" <<EOF
ffmpeg and ffprobe, as bundled with Dubcrate
============================================

FFmpeg $FFMPEG_VERSION (https://ffmpeg.org) and the LAME MP3 encoder $LAME_VERSION (https://lame.sourceforge.io), both under the
GNU Lesser General Public Licence (see the two licence files in this folder). FFmpeg here is built without --enable-gpl and
without --enable-nonfree; LAME is linked into it statically. Dubcrate only runs these programs; it does not link them.

Source: the exact sources are
  $FFMPEG_URL
  $LAME_URL
and the script that builds them, with every configure option, is scripts/build_ffmpeg_linux.sh in the Dubcrate source
(https://github.com/martincurkovic/Dubcrate). The configuration of these binaries is in ffmpeg-buildconf.txt. Anyone may
rebuild them from those and put their own ffmpeg and ffprobe in this folder, or install ffmpeg and ffprobe on their PATH
(Dubcrate uses a copy in this folder first, then PATH).
EOF

echo ""
echo "Done: $OUT"
ls -lh "$OUT/ffmpeg" "$OUT/ffprobe"
