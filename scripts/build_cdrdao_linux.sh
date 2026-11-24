#!/usr/bin/env bash
set -euo pipefail

# build_cdrdao_linux.sh - builds the cdrdao that the AppImage bundles, for the Linux this runs on (x86_64 or aarch64).
#
# Why: cdrdao is what burns on Linux (an audio CD with CD-TEXT, an MP3 disc as a data track), and a user should not have to find and
# install it. The distributions' builds link a pile of shared libraries (libao, libmad, libvorbis...) for formats Dubcrate never
# uses (it hands cdrdao WAV files and a disc image), so this one is built without them, with libstdc++ and libgcc linked in:
# nothing is needed but the C library, which every Linux has.
#
# cdrdao is GPL-2.0-or-later, which ffmpeg (LGPL) is not: so the bundle also carries the licence, the written offer of source, and
# the exact source it was built from (a GPL section 3 compliance: the source goes with the binary). Dubcrate only RUNS cdrdao, as
# a separate program; it does not link it.
#
# Output (build/cdrdao/<arch>/, not tracked): cdrdao and licenses/ (COPYING, README.txt, the source tarball). scripts/build_linux.sh
# calls this when that folder is missing and puts it in the same bundle as ffmpeg (dubcrate/bin).
#
#   scripts/build_cdrdao_linux.sh            build if needed
#   scripts/build_cdrdao_linux.sh --force    build again
#
# Needs a C and C++ compiler, make, curl, and autoconf, automake, libtool and pkg-config (the GitHub tag has no `configure`):
# `sudo apt install build-essential curl autoconf automake libtool pkg-config`. The download is checked against the SHA-256
# below; to move to a new version, change the version and the hash together (and the entry in
# src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md).

CDRDAO_VERSION="1.2.6"
CDRDAO_TAG="rel_1_2_6"
CDRDAO_URL="https://github.com/cdrdao/cdrdao/archive/refs/tags/$CDRDAO_TAG.tar.gz"
CDRDAO_SHA256="ba3eadcae7b62a709e9e23988d7fb41f822c408dcec9bd99ff1a343d1bcbc524"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m)"
WORK="$ROOT_DIR/build/cdrdao-work-$ARCH"
OUT="$ROOT_DIR/build/cdrdao/$ARCH"
JOBS="$(nproc)"

if [ "${1:-}" != "--force" ] && [ -x "$OUT/cdrdao" ]; then
    echo "cdrdao is already built: $OUT (use --force to build again)"
    exit 0
fi

for tool in autoreconf libtoolize pkg-config g++; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "$tool not found: sudo apt install build-essential curl autoconf automake libtool pkg-config" >&2
        exit 1
    }
done

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
mkdir -p "$WORK/src" "$OUT/licenses"
DOWNLOADS="$ROOT_DIR/build/downloads"
mkdir -p "$DOWNLOADS"
TARBALL="$DOWNLOADS/cdrdao-$CDRDAO_VERSION.tar.gz"
fetch "$CDRDAO_URL" "$CDRDAO_SHA256" "$TARBALL"
tar -xzf "$TARBALL" -C "$WORK/src"

echo "Building cdrdao $CDRDAO_VERSION ($ARCH)..."
cd "$WORK/src/cdrdao-$CDRDAO_TAG"
autoreconf -fi >"$WORK/cdrdao-autoreconf.log" 2>&1 || { tail -30 "$WORK/cdrdao-autoreconf.log"; exit 1; }
# no GUI (gcdmaster), no Ogg or MP3 reading, no LAME: the formats Dubcrate never gives it. libao (cdrdao's own audio playback,
# which Dubcrate never uses) is found by pkg-config and would be linked if this computer has it, so pkg-config is told to see
# nothing: configure then leaves it out, as it does for any library it can't find.
PKG_CONFIG_LIBDIR=/nonexistent PKG_CONFIG_PATH= ./configure --prefix="$WORK/prefix" \
    --without-gcdmaster --without-ogg-support --without-mp3-support --without-lame \
    CXXFLAGS="-O2 -std=gnu++14" LDFLAGS="-static-libstdc++ -static-libgcc" \
    >"$WORK/cdrdao-configure.log" 2>&1 || { tail -40 "$WORK/cdrdao-configure.log"; exit 1; }
make -j"$JOBS" >"$WORK/cdrdao-make.log" 2>&1 || { tail -40 "$WORK/cdrdao-make.log"; exit 1; }

cp dao/cdrdao "$OUT/cdrdao"
strip --strip-unneeded "$OUT/cdrdao"

# Checks: it runs, and depends on nothing but the C library
# (it prints its usage and fails when it is given nothing to do, hence the `|| true`)
SAID="$("$OUT/cdrdao" 2>&1 || true)"
grep -q "Cdrdao version $CDRDAO_VERSION" <<<"$SAID" || { echo "cdrdao does not report version $CDRDAO_VERSION" >&2; exit 1; }
if ldd "$OUT/cdrdao" | awk '{print $1}' | grep -v -e '^linux-vdso' -e '^libc\.so' -e '^libm\.so' -e '^libpthread\.so' -e '^libdl\.so' \
    -e '^librt\.so' -e '^/lib' -e '^ld-linux'; then
    echo "cdrdao links a library that is not part of every Linux (listed above)" >&2
    exit 1
fi

# What goes with it: the licence, the offer, and the source itself (GPL 2 section 3a)
cp COPYING "$OUT/licenses/cdrdao-COPYING.txt"
cp "$TARBALL" "$OUT/licenses/cdrdao-$CDRDAO_VERSION-source.tar.gz"
cat >"$OUT/licenses/cdrdao-README.txt" <<EOF
cdrdao, as bundled with Dubcrate
================================

cdrdao $CDRDAO_VERSION (https://github.com/cdrdao/cdrdao, https://cdrdao.sourceforge.net) under the GNU General Public Licence,
version 2 or later (cdrdao-COPYING.txt). It is the program Dubcrate runs to write a disc on Linux. Dubcrate runs it as a
separate program and does not link it; the rest of Dubcrate is not covered by this licence.

Source: the complete source this binary was built from is in this folder (cdrdao-$CDRDAO_VERSION-source.tar.gz, from
$CDRDAO_URL). The script that built it, with every configure option, is scripts/build_cdrdao_linux.sh in the Dubcrate source
(https://github.com/martincurkovic/Dubcrate): no GUI, no Ogg or MP3 reading, libstdc++ and libgcc linked in. Anyone may rebuild
it and put their own cdrdao in this folder, or install cdrdao and have it on their PATH (Dubcrate uses a copy in the app first,
then PATH).
EOF

echo ""
echo "Done: $OUT"
ls -lh "$OUT/cdrdao"
