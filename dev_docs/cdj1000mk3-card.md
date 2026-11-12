# CDJ-1000MK3 memory card: what is known

The goal is to pre-cache cue points, hot cues and (if it can be done) waveforms onto the SD
card in a CDJ-1000MK3, so a burned disc loads with them already set. The card format is
Pioneer's own and undocumented. This file records what is known, tagged like
[burning.md](burning.md): **[measured DATE]** from a file we have, **[notes]** from someone
else's notes and not checked, **[inferred]** a reading of the evidence that is likely but
not proven, **[unknown]**. **Nothing here has been checked against a real card yet.**

The rekordbox formats (`export.pdb`, ANLZ) do **not** apply: this player predates rekordbox
export. See [rekordbox-analysis.md](rekordbox-analysis.md) for those.

## Prior work

- **[notes]** [rdingwall/cdjmerge](https://github.com/rdingwall/cdjmerge) and its
  [wiki](https://github.com/rdingwall/cdjmerge/wiki/CDJ-1000MK3-SD-Card-File-Format-Info)
  (README May 2011, wiki 2015, no code). Hex-editor work on MP3 discs only, by its own
  account "probably contains mistakes". It gives: data in `PIONEER\MPJ\`; an index file
  `MPJINDX0.PDJ` with a header `PIONEER INDX....MP3 ..`; one `MPJ0xxxx.PDJ` per track; a
  "14-byte unique ID" in both; the ASCII `DISC`; a trailing `CSUM` plus 8 bytes. The card must
  be formatted by a camera or the player, not by Windows or the SD Association's tool.
  The firmware agrees with most of it (below) and corrects the ID size (16 bytes).
- The manual says a computer cannot copy or back up the card, and that discs, cues, loops
  and hot cues (A, B, C) are stored, 100 points total per disc.
- No other project covers this format. The commercial "CDJ-1000 Merger" is for the MK1 and
  MK2.

## The firmware

**[measured 2026-11-12]** `CDJ1000MK3_061023.EXE` (SHA-256
`d8198c249e443736dec00cb598b91c997be1171882c0e179fe40185b589ca5a2`) is a Windows updater,
an LHa self-extracting archive: `7zz x` opens it. It holds two files:

| File | Header | SHA-256 | Notes |
|---|---|---|---|
| `CDJ1K3MN.UPD` (1.48 MB) | `CDJ1K3  MAIN2.20`, inner name `DVJ-X1 upd` | `cd187e62...a11938` | The main program, version 2.20, build `20061021`. Readable once decoded. |
| `G11.UPD` (223 KB) | `CDJ1K3  DVPL0120`, inner name `G11.mot` | `27ee202f...4c1780` | Decodes to 128 KB (`0x000000`-`0x021000`) at 7.6 bits per byte: encrypted, compressed or a bitstream. **[unknown]** what it is. Not the card code. |

The program is shared with Pioneer's DVJ-X1 (strings say "Good Good Yamada! Bravo Bravo
DVJ!" and `DVJ-X1`), which is why the card code has `DVJ` as well as `CDJ` and `MPJ`.

### The `.UPD` transport

**[measured 2026-11-12]** Decoded; `spikes/cdj_upd_decode.py` does it. After an 18-byte
header line:

- Records start with `0x1f`, a tag (`10`, `18`, `2b`, or `22 54`), then a 3-byte address
  (steps of 16), 16 data bytes and 1 check byte. 61,012 data records in the main file; every
  one decodes to exactly 20 bytes, with no two records disagreeing about an address. Two
  records at the end are not data.
- **The byte map** (plain byte to stored byte):

  | Plain | Stored |
  |---|---|
  | `00`-`9f` | `plain + 0x40` (`40`-`df`) |
  | `a0`-`df` | `s = plain - 0xa0`; one byte `s` if `s < 8`, else two bytes `0x3a + (s >> 4)`, `0x30 + (s & 15)` |
  | `e0`-`ff` | unchanged (so raw `ff` is the flash's erased `0xff`) |

  This is a **bijection**, which is why the stream survives: the framing byte `0x1f` and the
  digits never occur as plain data.
- **An earlier version of this note had the wrong map** (a plain `+0x40` for everything, with
  a `0xc0`-`0xff` escape), which looked right for strings and addresses but corrupted every
  opcode byte `>= 0xa0`. What decided it: SuperH opcodes valid at 89.9% vs 97.9%, `0xFxxx`
  (FPU) opcodes at 8.4% vs 0.5%, `mov.l @(disp,pc)` at 0.9% vs 8.4%, and the image ending at
  a round `0x1c0000`. Pointer consistency could **not** tell the maps apart, because both
  shift every escaped byte alike. Treat any result that rests on bytes `>= 0xa0` with care
  until a real card or a second dump confirms it.
- The check byte is **[unknown]** (not a plain sum). It only matters for writing an update,
  which we won't do.

### The image

**[measured 2026-11-12]** The main program decodes to 20 segments between `0x020100` and
`0x1c0000`, about 976 KB of data in a 1.75 MB span. It is a flat image with no symbols, and
the addresses in the file are not run addresses: 355 of its 520 strings are pointed to by
32-bit words equal to `file address + 0xabfe0000` (the next best offset explains 49), so it
runs based at **`0xAC000100`**. That is the SH-4's *uncached* (P2) mirror of its SDRAM area,
and function and data pointers use the same memory through `0xCC…` (`0x20000000` higher), so
both prefixes appear in the code. Data the firmware uses at run time lives past the end of
the image, in RAM (for example `0xCC244BA4`), so it can't be read from the file.

The CPU is a big-endian **SH-4**. In Ghidra 12.2 (`SuperH4:BE:32:default`) the image gives
297,914 instructions with no decode errors; SH-2 gives 296,018 with a few, so the code uses
the FPU hardly at all, and the `0xAC…` base is what points to the SH-4. The runtime is a
µITRON-style RTOS (task and message-buffer names such as `dly_tsk`, `wai_sem`, `rcv_mbf`).
The firmware has **debug strings and state names left in**, so function and mode names can be
found by searching strings.

## The card format, from the firmware

All **[measured 2026-11-12]** from the decompiled code unless marked; run addresses unless
marked. **[inferred]** means a reading of the code, not yet seen in a real file.

### Names

A table of space-padded names at about `0xAC00CC20`: `PIONEER`, `DVJ`, `CDJ`, `MPJ`,
`DVJINDX`, `CDJINDX`, `MPJINDX`, `THUM`, `.PDJ`, `.*`, `.BAK`. So there are **three media
kinds** (`DVJ`, `CDJ`, `MPJ`), each with an index file and track files, all `.PDJ`, and a
`.BAK` for backups. cdjmerge only saw `MPJ` (MP3 discs). **[inferred]** The audio-CD files,
the ones Dubcrate cares most about, are `CDJINDX0.PDJ` and `CDJ0xxxx.PDJ`. `THUM` is probably
DVD thumbnails.

### Chunks

The files are made of **chunks: a 4-character tag, a big-endian `u32`, then the payload**.
The tags (at `0xAC00CA84`...): `PIONEER␠`, `INDX`, `INFO`, `THUM`, `DID␠`, `CN11`, `HC11`,
`W001`, `W002`, `CSUM`, and the media words `CD␠␠` and `MP3␠`. The `u32` is not always a
length: for `DID␠` it is `0x10` (the payload length), for `W002` it is `0x38` (a record size).

**Index file** (`INDX`), built by `0xAC0E47DC` (CD) and `0xAC0E5BD2` (MP3):

| Offset | Content |
|---|---|
| `0x00` | `PIONEER␠` |
| `0x08` | `INDX` |
| `0x0C` | `u32`, from a per-media table kept in RAM **[unknown]** |
| `0x10` | media word, `CD␠␠` or `MP3␠` |
| `0x14` | `u32`, a 16-bit argument (probably a count) **[inferred]** |

That matches cdjmerge's `PIONEER INDX....MP3 ..`, and shows the header is really 0x18 bytes.

**Track file**, built by `0xAC0E4996` (CD) and `0xAC0E5DA6` (MP3), as a fixed-layout buffer:

| Chunk | CD offset | MP3 offset | Notes |
|---|---|---|---|
| `PIONEER␠` | `0x00` | `0x00` | |
| `INFO` | `0x08` | `0x08` | `u32` at `0x0C`, from the RAM table |
| `DID␠` | `0x10` | `0x10` | `u32` = `0x10`, then **16 bytes** at `0x18`: the disc ID, copied from RAM (`0xCC244BA4`) |
| `CN11` | `0x28` | `0x28` | |
| `HC11` | `0x804` | `0x33C4` | |
| `W001` | `0x848` | `0x3558` | |
| `W002` | `0x1DF8` | not read | |
| `CSUM` | after the data | after the data | 12 bytes |

**[inferred]** From the sizes between those offsets: `CN11` is `100 x 20 + 4` bytes on CD and
`100 x 132 + 4` on MP3 (matching the manual's **100** cue and loop points per disc), and `HC11`
is `3 x 20` on CD and `3 x 132` on MP3 (**hot cues A, B and C**). An MP3 entry is 132 bytes
rather than 20, perhaps because it carries more (a file name or position). `W001` is about
5.5 KB on CD. The `W002` writer (`0xAC0E6EF2`) builds records of `0x38` = 56 bytes (a `u16`
index at `+0`, 50 bytes at `+6`) for 10 entries, which looks like text (labels), **not**
waveform data, so cdjmerge's "wave data" guess may be wrong. The layout inside every chunk is
**[unknown]**.

**Checksum.** `0xAC0E46F0` adds up the buffer as **16-bit big-endian words** (`len / 2` of
them) in a 32-bit accumulator. The caller (`0xAC0E8EB8`, the card task's message handler) then
writes a `CSUM` chunk at `buffer + (len & ~1)` through `0xAC0E5000`, which builds
`CSUM`, `u32 = 2`, the low **16 bits** of the sum, and copies 12 bytes, so the last 2 are
whatever was on the stack. **[inferred]** The sum covers every byte before the `CSUM` chunk,
including `PIONEER`. cdjmerge's "`CSUM` followed by an 8-byte value" is consistent with 12 bytes.
Not yet checked against a real file.

### Where the code is

| Function | Does |
|---|---|
| `0xAC0E4438`, `0xAC0E4488` | compare a 4-byte tag with the name for an id (3 and 6 of them) |
| `0xAC0E47DC`, `0xAC0E5BD2` | build the index header (CD, MP3) |
| `0xAC0E4996`, `0xAC0E5DA6` | build the track-file template (CD, MP3) |
| `0xAC0E46F0` | the checksum |
| `0xAC0E5000`, `0xAC0E65D4` | write the `CSUM` chunk |
| `0xAC0E8EB8` | the card task's message handler (calls the checksum and the writer) |
| `0xAC0E489A`, `0xAC0DE978`, `0xAC0E2008`, `0xAC0E25DA` | copy and compare the 16-byte disc ID with the one in RAM; type 2 is CD (ID at `+4` in the state record) and 3 is MP3 (at `+0x14`) |

The ID buffer is filled **elsewhere**: these functions only use it. Nothing was found that
calls the template builders by address or by pointer, so they are reached some other way
(a table of offsets, or code Ghidra hasn't disassembled).

### What else the program shows

- Tasks: `Card Navi`, `Card Receive`, `Card Sprt`, `Card Capture`, `CDCUEREC`,
  `MDSPWAVE`. Modes: `MG_MAIN_CUE_SAVE_MODE`, `HOTCUE_REC_MODE`, `HOTCUE_REC_TIME`,
  `CARDCOPY_MODE`, `CARDIN`, `CARD_CAMEBACK_A/B/C`, `CUE_ROAD_ISYTYPE`, `ROAD_CUE`,
  `TEXT_WAVE_MODE`. Disc kinds: `CdDiscInfo`, `Mp3DiscInfo`, `CdOldDiscInfo`,
  `Mp3OldDiscInfo`, `DISC_KIND`, `DISC DELETE?`.
- Messages: `MEMORY - CARD FULL / PROTECTED / NO CARD / UNFORMAT / CARD ERROR`,
  `CUE FOLDER00`, `HOT CUE FOLDER00`, `INTERNAL MEMORY`, `CARD MEMORY`, `Disc Type: MP3`,
  `Disc Name:`. "Old" disc info suggests an earlier ID scheme is still read.

## Still unknown

1. **How the 16-byte disc ID is made** (TOC, track offsets, a hash?), and what writes the RAM
   buffer at `0xCC244BA4`. It is the blocker for pre-caching, because the card entry must be
   found by the player.
2. The layout inside `INFO`, `CN11`, `HC11`, `W001` and `W002`: entry fields, time units
   (cdjmerge guesses 1/75 s), cue colours and flags, hot cue slots.
3. Whether the `CSUM` reading above is right, and whether the sum starts at `PIONEER`.
4. Whether waveform data (if `W001` is it) can be generated by Dubcrate or only captured.
5. File naming and numbering (`CDJ0xxxx.PDJ`) and how the index maps IDs to files.
6. What `G11.UPD` is.

## Plan for when the player is available

1. Format a card in the player (not on the Mac). Copy `PIONEER/` off it as a baseline
   (read-only: mount the card read-only and copy, never edit in place).
2. Use discs you already have (no blanks needed for this): load a disc, set one thing, copy
   the card, diff. Do it for: loading only, one memory cue, one hot cue A, a loop; change one
   value at a time (a cue 1 s later) to find the time unit and byte order.
3. **Check the `CSUM` reading first.** It is the cheapest test: sum a real `.PDJ` as 16-bit
   big-endian words up to the `CSUM` chunk and compare. If it holds, the byte map and the
   decompile are both on firmer ground.
4. Check the chunk offsets above against a real CD file (`DID␠` at `0x10`, `CN11` at `0x28`,
   `HC11` at `0x804`), and the chunk sizes.
5. Use several different discs to gather IDs, then find the ID code in the firmware (start
   from who writes `0xCC244BA4`) and reproduce it.
6. Write a pure parser in the package (tests with a captured card), then a writer, and test
   the writer by loading the card in the player.

## Reproducing the analysis

```sh
mkdir -p /tmp/cdj-fw && cp ~/Downloads/CDJ1000MK3_061023.EXE /tmp/cdj-fw/
cd /tmp/cdj-fw && 7zz x CDJ1000MK3_061023.EXE            # CDJ1K3MN.UPD, G11.UPD
uv run --project ~/Development/Dubcrate \
    ~/Development/Dubcrate/spikes/cdj_upd_decode.py CDJ1K3MN.UPD -o main.bin
strings -n 6 main.bin | less
```

Ghidra (12.2, via Homebrew): import `main.bin` as a raw binary with language
`SuperH4:BE:32:default` and base `0xac000100`, then run `spikes/ghidra/SeedFunctions.java` as a
pre-script (it makes a function at every `sts.l pr,@-r15`; the raw image has no entry point, so
analysis finds nothing without it). Headless:

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home
analyzeHeadless /tmp/gh proj -import main.bin -loader BinaryLoader -loader-baseAddr 0xac000100 \
    -processor "SuperH4:BE:32:default" -scriptPath ~/Development/Dubcrate/spikes/ghidra \
    -preScript SeedFunctions.java
analyzeHeadless /tmp/gh proj -process main.bin -noanalysis -readOnly \
    -scriptPath ~/Development/Dubcrate/spikes/ghidra -postScript TagUsers.java decompile
```

`DecompAt.java ADDR...` decompiles the function at each address; `PtrUsers.java ADDR...` finds
the code that loads a pointer to it (add `raw:` to skip the `0xCC` mirror). Function calls
through a literal pool (`mov.l @(disp,pc),r3; jsr @r3`) are not followed by Ghidra on its own.

Keep the firmware and anything decoded from it **out of the repository** (it is Pioneer's
copyright; this file records findings in our own words). Reading it to learn the card
format is interoperability research, but check your local rules before you publish.
