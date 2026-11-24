#!/usr/bin/env bash
set -euo pipefail

# build_cdrdao_windows.sh - builds the cdrdao.exe that the Windows installer bundles (64-bit Windows).
#
# Run it from an MSYS2 "MSYS" shell (scripts/build_windows.ps1 does that for you), NOT the MINGW64 one: cdrdao is a Unix program (it
# uses signals, fork-style process control, fcntl, sys/wait.h...) that upstream supports on Windows only through a POSIX layer
# (Cygwin), and MSYS2's "MSYS" environment is that layer. A native MinGW build fails on the first dozen of those. cdrdao's own Windows
# SCSI code (`ScsiIf-nt.cc`, the SCSI pass-through the system gives any program that may open the drive) is used, selected by telling
# configure the host is Cygwin.
#
# Like scripts/build_cdrdao_linux.sh: no GUI, no audio-format libraries, libstdc++ and libgcc linked in. What it still needs is the POSIX
# layer's own DLL (msys-2.0.dll, the Cygwin runtime: LGPL-3.0-or-later with an exception for linking GPL programs) and msys-iconv-2.dll
# (GNU libiconv, LGPL-2.1-or-later), which are copied beside it. cdrdao is GPL-2.0-or-later, so the bundle also carries the licences, the
# written offer of source, and the exact source it was built from (GPL section 3). Dubcrate only RUNS cdrdao, as a separate program.
#
# Output (build/cdrdao/x86_64/, not tracked): cdrdao.exe, the two DLLs, and licenses/. scripts/build_windows.ps1 calls this when that
# folder is missing and bundles it as dubcrate/bin.
#
#   scripts/build_cdrdao_windows.sh            build if needed
#   scripts/build_cdrdao_windows.sh --force    build again
#
# Needs, in MSYS2 (https://www.msys2.org): pacman -S --needed base-devel gcc make autoconf automake libtool pkgconf diffutils
#     libiconv-devel gettext-devel curl. The download is checked against the SHA-256 below; to move to a new version, change the version and
# the hash together (and the entry in src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md).
# Whether the drive can be used is only known on a real drive: see TODO.md.

CDRDAO_VERSION="1.2.6"
CDRDAO_TAG="rel_1_2_6"
CDRDAO_URL="https://github.com/cdrdao/cdrdao/archive/refs/tags/$CDRDAO_TAG.tar.gz"
CDRDAO_SHA256="ba3eadcae7b62a709e9e23988d7fb41f822c408dcec9bd99ff1a343d1bcbc524"

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="x86_64"
WORK="$ROOT_DIR/build/cdrdao-work-windows"
OUT="$ROOT_DIR/build/cdrdao/$ARCH"
JOBS="$(nproc)"

if [ "${MSYSTEM:-}" != "MSYS" ]; then
    echo "Run this from an MSYS2 MSYS shell (MSYSTEM is '${MSYSTEM:-unset}', not MSYS: the MINGW64 one can't build cdrdao)." >&2
    exit 1
fi

if [ "${1:-}" != "--force" ] && [ -x "$OUT/cdrdao.exe" ]; then
    echo "cdrdao is already built: $OUT (use --force to build again)"
    exit 0
fi

for tool in autoreconf libtoolize pkg-config g++ curl; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "$tool not found: pacman -S --needed base-devel gcc make autoconf automake libtool pkgconf diffutils libiconv-devel gettext-devel curl" >&2
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
# no GUI (gcdmaster), no Ogg or MP3 reading, no LAME: the formats Dubcrate never gives it. libao (cdrdao's own audio playback) is
# found by pkg-config and would be linked if this computer has it, so pkg-config is told to see nothing. `-include cstdint` is for
# `int16_t` in lec.cc, which a current compiler no longer gets from another header. The host is named Cygwin so configure picks
# `ScsiIf-nt.cc` (MSYS2's own triple, x86_64-pc-msys, is not one it knows).
PKG_CONFIG_LIBDIR=/nonexistent PKG_CONFIG_PATH= ./configure --prefix="$WORK/prefix" \
    --build=x86_64-pc-cygwin --host=x86_64-pc-cygwin \
    --without-gcdmaster --without-ogg-support --without-mp3-support --without-lame \
    CXXFLAGS="-O2 -std=gnu++14 -include cstdint" LDFLAGS="-static-libstdc++ -static-libgcc" \
    >"$WORK/cdrdao-configure.log" 2>&1 || { tail -40 "$WORK/cdrdao-configure.log"; exit 1; }
make -j"$JOBS" >"$WORK/cdrdao-make.log" 2>&1 || { tail -40 "$WORK/cdrdao-make.log"; exit 1; }

cp dao/cdrdao.exe "$OUT/cdrdao.exe"
# (not stripped: `strip --strip-unneeded` made this Cygwin-ABI program print nothing and exit, seen 2026-11-24)
cp /usr/bin/msys-2.0.dll /usr/bin/msys-iconv-2.dll "$OUT/"

# Checks: it runs (with its DLLs beside it), and needs no DLL beyond those two and the ones every Windows has
# (it prints its usage and fails when it is given nothing to do, hence the `|| true`)
# (started through cmd.exe, as Windows will start it: run straight from this MSYS shell, the copy of msys-2.0.dll beside it is a second
# Cygwin runtime next to the shell's own, and the program is not found, status 127, seen 2026-11-24)
SAID="$(cmd.exe //c "$(cygpath -w "$OUT/cdrdao.exe")" 2>&1 || true)"
grep -q "Cdrdao version $CDRDAO_VERSION" <<<"$SAID" || { echo "cdrdao does not report version $CDRDAO_VERSION" >&2; exit 1; }
if objdump -p "$OUT/cdrdao.exe" | awk '/DLL Name:/ {print tolower($3)}' |
    grep -v -E '^(msys-2\.0|msys-iconv-2|kernel32|msvcrt|ucrtbase|user32|advapi32|shell32|ole32|oleaut32|ws2_32|bcrypt|ntdll|gdi32|shlwapi|crypt32|secur32|userenv|psapi|api-ms-win-crt-[a-z0-9-]+)\.dll$'; then
    echo "cdrdao.exe needs a DLL that is not bundled and not part of every Windows (listed above)" >&2
    exit 1
fi

# What goes with it: the licences, the offer, and the source itself (GPL 2 section 3a)
cp COPYING "$OUT/licenses/cdrdao-COPYING.txt"
cp "$TARBALL" "$OUT/licenses/cdrdao-$CDRDAO_VERSION-source.tar.gz"
for dir in msys2-runtime libiconv; do
    for file in /usr/share/licenses/$dir/*; do
        [ -f "$file" ] && cp "$file" "$OUT/licenses/$dir-$(basename "$file").txt"
    done
done
cat >"$OUT/licenses/cdrdao-README.txt" <<EOF
cdrdao, as bundled with Dubcrate
================================

cdrdao $CDRDAO_VERSION (https://github.com/cdrdao/cdrdao, https://cdrdao.sourceforge.net) under the GNU General Public Licence,
version 2 or later (cdrdao-COPYING.txt). It is the program Dubcrate runs to write a disc on Windows. Dubcrate runs it as a
separate program and does not link it; the rest of Dubcrate is not covered by this licence.

Source: the complete source this binary was built from is in this folder (cdrdao-$CDRDAO_VERSION-source.tar.gz, from
$CDRDAO_URL). The script that built it, with every configure option, is scripts/build_cdrdao_windows.sh in the Dubcrate source
(https://github.com/martincurkovic/Dubcrate): no GUI, no Ogg or MP3 reading, libstdc++ and libgcc linked in.

Two DLLs sit beside cdrdao.exe, both from MSYS2 (https://www.msys2.org):
  msys-2.0.dll       the Cygwin POSIX runtime, GNU Lesser General Public Licence 3 or later with an exception for linking
                     (https://github.com/msys2/msys2-runtime, https://cygwin.com); its licence files are msys2-runtime-*.txt here
  msys-iconv-2.dll   GNU libiconv, GNU Lesser General Public Licence 2.1 or later (https://www.gnu.org/software/libiconv/);
                     its licence files are libiconv-*.txt here
Anyone may rebuild cdrdao and put their own cdrdao.exe and DLLs in this folder, or install cdrdao and have it on their PATH
(Dubcrate uses a copy in the app first, then PATH).
EOF

echo ""
echo "Done: $OUT"
ls -lh "$OUT/cdrdao.exe" "$OUT/msys-2.0.dll" "$OUT/msys-iconv-2.dll"
