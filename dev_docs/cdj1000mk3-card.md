# CDJ-1000MK3 memory card: what is known

The goal is to pre-cache cue points, hot cues and (if it can be done) waveforms onto the SD
card in a CDJ-1000MK3, so a burned disc loads with them already set. The card format is
Pioneer's own and undocumented. This file records what is known, tagged like
[burning.md](burning.md): **[measured DATE]** from a file we have, **[notes]** from someone
else's notes and not checked, **[inferred]** a reading of the evidence that is likely but
not proven, **[unknown]**. **The section "A real card" below is measured from a real card; the
firmware sections above it were checked against it only where that section says so.** Where
they disagree (the fixed offsets), the real card wins.

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

> **Superseded in part by the real card ([A real card](#a-real-card)).** Right: the chunk tags,
> `DID ` at `0x10`, the 16-byte ID, `CN11` as 20-byte cue entries, `HC11` as 3 x 20 bytes, the
> checksum. **Wrong:** the fixed offsets (`HC11` at `0x804` and the rest are not in the files:
> they are sized to what is stored), `CN11` as `100 x 20 + 4` bytes, and `W002` as labels (both
> `W001` and `W002` are waveform). The ID chase below ended in the real card: the ID is the TOC.

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

**How far the ID chase got** (**[measured 2026-11-12]**). The 16-byte buffers at `0xCC244BA4`
(CD) and `0xCC244BB4` (MP3) are filled by `0xAC103F64` and `0xAC103F86`, which only copy 16
bytes out of two longer-lived RAM records, `0xCC2DE4EC` (CD) and `0xCC30BF74` (MP3), and return
0 when the record's first `u16` is zero (so that `u16` is a "valid" flag). A reset routine
(`0xAC10355E`) copies the current record to an "old" slot 16 bytes on, then clears it, which is
why the firmware has `Old` disc-info types. Nothing found so far *writes* a new ID into those
records: the writer is reached through code Ghidra has not disassembled (a large block of small
accessor functions around `0xAC103D00`-`0xAC104400` is only partly recognised). The disc's TOC
is read by `packetReadTOC` (`0xAC1262A4`) in the standard ATAPI layout (first and last track,
then 8-byte entries with track number, ADR/control and minute/second/frame), and the debug
print `0xAC127014` shows the fields; **[inferred]** the ID is derived from the TOC, but that
is a guess. A "Disc Name" of 8 bytes is also kept (`0xAC0E1C0C`), possibly the first half of
the ID.

### What else the program shows

- Tasks: `Card Navi`, `Card Receive`, `Card Sprt`, `Card Capture`, `CDCUEREC`,
  `MDSPWAVE`. Modes: `MG_MAIN_CUE_SAVE_MODE`, `HOTCUE_REC_MODE`, `HOTCUE_REC_TIME`,
  `CARDCOPY_MODE`, `CARDIN`, `CARD_CAMEBACK_A/B/C`, `CUE_ROAD_ISYTYPE`, `ROAD_CUE`,
  `TEXT_WAVE_MODE`. Disc kinds: `CdDiscInfo`, `Mp3DiscInfo`, `CdOldDiscInfo`,
  `Mp3OldDiscInfo`, `DISC_KIND`, `DISC DELETE?`.
- Messages: `MEMORY - CARD FULL / PROTECTED / NO CARD / UNFORMAT / CARD ERROR`,
  `CUE FOLDER00`, `HOT CUE FOLDER00`, `INTERNAL MEMORY`, `CARD MEMORY`, `Disc Type: MP3`,
  `Disc Name:`. "Old" disc info suggests an earlier ID scheme is still read.

## A real card

Everything in this section is **[measured 2026-11-13]** from a real card written by the owner's
CDJ-1000MK3 unless it is tagged **[inferred]** or **[unknown]**. Where it disagrees with the
firmware sections above (the fixed offsets, the meaning of `W002`), the real card wins.

### How it was captured

- The card is 2 GB, FAT16, volume `DISK_IMG`, formatted **by the player**. A freshly formatted
  card is **empty**: `PIONEER/CDJ/` appears only when the player first saves something. (The
  Mac wrote `.Spotlight-V100` and `.fseventsd` on the card when it first mounted it, before the
  first image; the player did not mind. Never mount the real card again: work on images.)
- `spikes/cdj_card_snap.sh NAME [/dev/diskN]` unmounts the card and reads the raw device into
  `~/cdj-card/NAME.img` (outside the repo: they are the owner's data) with a progress bar. It
  needs `sudo` in a real Terminal, `pv`, and the whole-disk device if a card reader shows a
  second small disk.
- To read an image: `hdiutil attach -readonly -nomount -imagekey diskimage-class=CRawDiskImage
  X.img` (**the disk number it prints changes**), then `diskutil mount readOnly diskNs1`, copy
  `PIONEER/CDJ/*.PDJ` out, `hdiutil detach diskN`. The files carry the date 1 Jan 1970: the
  player writes no timestamps.
- `spikes/cdj_card_dump.py dump|diff` decodes files or a whole card with `dubcrate.cdj_card`.
- Each disc's TOC came from `drutil toc` on the Mac (saved as `~/cdj-card/CD1 TOC.txt` etc.; copies
  are in `tests/data/cdj_card/` with the real files the tests use).

### The experiments (images in `~/cdj-card/`, all 2026-11-13)

One thing changed at a time, on audio CDs the owner already had (none burned by Dubcrate).
CD 1 is 12 tracks, CD 2 is 12 tracks, CD 3 is 20.

| Image | What was done on the player | What it showed |
|---|---|---|
| `00-blank` | card formatted, nothing else | empty FAT16 |
| `bad-01` | a mistake, discarded | not used |
| `02-cue-1` | CD 1 loaded; memory cue at exactly 5 s into track 1 | the two files appear; cue = 525 |
| `03-cue-1b` | + memory cue at 10 s, track 1; track 1 played on | 2nd entry (900); a waveform appears |
| `04-hotcue-A` | + hot cue A at 15 s, track 1 | `HC11` slot A, position 1275 |
| `05-memorycue-track2-4sec-1min4sec` | + memory cues at 4 s and 1:04, track 2 | positions are disc-wide |
| `06-hotcueB-track3-2secs` | + hot cue B at 2 s, track 3 (played briefly) | slot number = A 1, B 2; waveform part-filled |
| `07-memcue-track1-2sec` (made as `04-`, renamed by the owner) | + memory cue at 2 s, track 1 | cues kept in the order set, not sorted |
| `08-loop-track1-approx40-42sec` | + a loop, by ear, about 40 to 42 s, track 1 | loop = a `CN11` entry, kind `0x20` |
| `09-second-cd-nothing-set` | CD 2 loaded, nothing set | an index entry, no file |
| `10-third-cd-20-tracks` | CD 3 loaded (and, **[inferred]** from the files, track 1 of CD 2 and 3 played) | files for 2 and 3; 20-track ID |
| `t4` (no image: **`dubcrate card sync 4 --card /Volumes/DISK_IMG --from-drive --yes`** run by the owner, 2026-11-14, on a **second card** (see below) the player had set up with CD 1) | **the whole path, by our own command**: a throwaway Dubcrate audio disc of 20 tracks (rekordbox cues, 81 of them) synced to a commercial 20-track CD in the Mac's drive, whose real TOC `--from-drive` read; a new index entry and a new file, written after a backup, flushed, then ejected | **accepted: all 81 cues showed** on the player. The files copied back off the card (`~/cdj-card/t4-post-cdj`): our index entry unchanged (72 bytes, two discs), `CDJ00002.PDJ` rewritten by the player from our file to 4,006 bytes: **all 81 cues kept** in our order, hot cues empty (as intended), and waveform records for the three tracks played (1, 2 and 20; the slots between are empty, track 0), so slots are indeed made **up to the highest track played**. Our parser round-trips the player's file byte for byte |
| `t3-new-disc` (same tooling and day; `CD4 TOC.txt` is the `drutil toc` of a fourth audio CD the card had never seen) | **a disc not in the index at all**: we added its index entry **and** its file (memory cues at 20 s in track 1 and 45.5 s in track 2, a loop 50 to 52 s in track 1), on top of the three-disc image `10-third-cd-20-tracks` | **accepted** (owner, on the player): the cue at 20 s and the loop in track 1, and the cue in track 2 at "45 s 37 frames" (45.5 s is 45 s and 37.5 frames). The files copied back off the card (`~/cdj-card/t3-post-cdj`): the index (120 bytes) and CD 1 to 3's files are **byte for byte the image's**, so the player left our new index entry alone; `CDJ00004.PDJ` grew from 206 to 430 bytes by **one `W001` and one `W002` record for each of the two tracks played** (tracks 1 and 2), our three cues unchanged |
| `t2-cd2-file-from-scratch` (same tooling and day) | **a file made from nothing** for CD 2, which had an index entry and no file: three memory cues (15 s in track 1, 20 s in track 2, 30 s in track 12), **no waveform records** | **accepted**: the three cues were where expected. The files copied back off the card (`~/cdj-card/t2-post-cdj`): our three cues and the index are **kept exactly**, but the player **rewrote `CDJ00002.PDJ` from 206 to 1,550 bytes**: it added **12 `W001` and 12 `W002` records**: slots for every track **up to the highest one played** (an unplayed slot has track 0 and no data), filled for the tracks that were played (track 1, 38 of 50 columns; track 12, 3 columns, after jumping to its cue). In `t3` only tracks 1 and 2 were played, and only two records of each kind were added, which fits (**[inferred]** from these two, not tested on its own). `cdj_card` parses the player's file and writes it back **byte for byte** |
| `t1-cues-swapped-cd1` (written by `spikes/cdj_card_testimage.py`, put on the card with `cdj_card_restore.sh`; 2026-11-14, firmware 2.20) | **our writer's file**: CD 1's cues replaced (memory cues at 20 s and 30 s in track 1, 45 s in track 2, 10 s in track 3, a loop 50 to 52 s); hot cues A, B and the waveforms kept | **accepted**: the owner loaded CD 1 and the new cues and the loop were there, no card error. The files copied back off the card afterwards (`~/cdj-card/t1-post-cdj`) are **byte for byte what was written**: the player rewrote nothing, though a track was played (its waveform already existed) |
| `wt1` (`write-test-1.img` made by `spikes/cdj_card_wavetest.py`, flashed to the spare card; played; imaged as `cdj-waveform-test-3`; 2026-11-15) | **a commercial 20-track CD (`CDJ00005`) with waveform records written by us**: track 1 a sawtooth (1 to 15), track 2 the model's values, track 3 nothing, track 4 the model's first 50 columns and 0 after; played: track 1 about 40 s, track 2 about 60 s, track 3 and 4 on | **accepted, used and kept**: no card error; tracks 1, 2 and 4 loaded at once with no on-player analysis, track 3 (no record) was analysed on the player at load and the player then wrote its own record. After play, the file's other discs and the index are **byte for byte unchanged**; in `CDJ00005.PDJ` tracks 1 and 2 are **identical, 100 of 100 columns, to what we wrote** (the player did not re-measure the columns it played), track 4's written half is unchanged and the player filled **only the zero columns it played** (columns 50 to 56; the rest of the second half still 0), and the unplayed slot of track 3 got its track number and a full record. Only the 2 pad bytes after the checksum and the slot changed. Track 3, the player's own values for music the model never saw: **91% within one value, 96% within two, mean error 0.73, bias +0.13**. **The owner saw the sawtooth on track 1's waveform** (the screen draws the stored record); tracks 2 and 4 looked like ordinary waveforms. Track 4 was played at the start and then scrubbed to the end, and the player filled columns 50 to 56 of the empty half (it never changes a column that already has a value) |

### Files and chunks

Every file is `PIONEER ` then chunks of `tag(4)`, `u32`, payload, all big-endian. The files are
**sized to what is stored**, not the fixed-layout templates in the firmware sections.

**`CDJINDX0.PDJ`**: `PIONEER ` `INDX` `u32`=1, `CD  `, `u32` disc count, then 24 bytes per
disc: the file's name (`CDJ00001`, no extension) and the 16-byte disc ID. 48 bytes for one disc,
+24 for each more. A disc is added when it is loaded, before it has a file.

**`CDJ0000n.PDJ`** (one per disc), in this order:

| Chunk | `u32` | Payload |
|---|---|---|
| `INFO` | 2 (**[unknown]**) | none |
| `DID ` | 16 | the disc ID |
| `CN11` | payload length | none if no cues; else `u16` count + 20 bytes per cue or loop |
| `HC11` | 0, or 60 once a hot cue is set (**[unknown]**) | **always 60 bytes**: 3 slots x 20, zeros if unset |
| `W001` | payload length | 56 bytes per record (waveform, coarse) |
| `W002` | payload length | 56 bytes per record (waveform, fine) |
| `CSUM` | 2 | `u16` checksum, then 2 junk bytes |

**`CSUM`** is the sum of every **16-bit big-endian word before the `CSUM` tag**, mod 65536 (checked
on every disc file in the ten good images). The 2 bytes after it are leftover stack, different in every file
(`0000`, `8f5e`, `007a`, `0127`): a writer can use zeros.

### The disc ID is the disc's TOC

The 16 bytes are `u16` track count, `u16` flags, `u32` lead-out, `u32` start of track 2, `u32`
start of the last track, all in **absolute frames** (`(minutes x 60 + seconds) x 75 + frames`,
so track 1 is at 150). Checked against `drutil toc` of three discs, the third (20 tracks) as a
prediction made before reading its TOC:

| | CD 1 (12 tracks) | CD 2 (12) | CD 3 (20) |
|---|---|---|---|
| Track count | 12 | 12 | 20 |
| Lead-out | 350,683 (77:55.58) | 360,580 (80:07.55) | 351,733 (78:09.58) |
| Track 2 starts | 28,905 | 32,550 | 14,928 |
| Last track starts | 323,803 | 335,206 | 336,290 |

It uses **no audio**, so Dubcrate can compute a disc's ID from its planned track lengths before
it is burned. The flags word is `0x0000` in the index and `0x0020` in a disc's own file
(**[unknown]** meaning); everything else is the same, so a disc is matched on the other 14 bytes.

### Memory cues, loops and hot cues

All positions are **absolute frames: the track's start (from the TOC) plus the time in the
track**, in 1/75 s. A cue 5 s into track 1 is `150 + 375 = 525`; 4 s into track 2 of CD 1 is
`28,905 + 300 = 29,205`. The track number is stored as well.

**`CN11` entry (20 bytes)**: `u32` index (0, 1, ... in the order they were set; the file keeps
that order, **not** position order), `u16` kind (`0x0010` memory cue, `0x0020` loop), `u32`
position (a loop's start), `u32` end (a loop's end; 0 for a cue), `u16` track, `u32` flag (0 for a
cue, `0x00010000` for the loop seen; **[unknown]**). The loop (about 40.2 s to 42.9 s, set by ear)
was stored as `(5, 0x20, 3165, 3365, 1, 0x00010000)`.

**`HC11` slot (20 bytes, three: A, B, C)**: `u16` slot (A = 1, B = 2, C = 3 **[inferred]**,
not seen), `u16` kind (`0x0010`), `u32` position, `u32` end (0), `u16` track, `u32` 0, `u16` 0.
That is a `CN11` entry minus the leading `u16`, so the position is at +4 in a slot, +6 in a cue.
An unset slot is all zeros. A at 15 s into track 1 was `0001 0010 000004fb ...` (1275); B at 2 s
into track 3 was `0002 0010 0000f00e ... 0003` (61,454).

### The waveform

`W001` and `W002` hold **per-track waveform records, written as the track plays**: a record is
`u16` track, 4 zero bytes, then 50 bytes. **`W002` is the waveform: 100 columns, 1/100 of the track
each, one value 0 to 15 per column** (a nibble), packed two to a byte with **the LOW nibble the earlier
column**. `W001` is the same at half the resolution: 50 bytes, each about the level of the mean amplitude
of its pair of columns (`round(10 log10((10^(a/10) + 10^(b/10)) / 2))`, equal for 93% of pairs and within
one for the rest). A track that was **never played has no record** (its slot is track 0, all zero); a track
**played only part of the way has the columns reached so far filled and the rest zero**; **a column
that has a value is never rewritten**. The screen draws these values (a record we wrote showed on it).
What the values mean, measured, is in "The waveform, measured" below.

**What the firmware says about it (2026-11-14, a first pass; the amplitude scale is still not found):**

- The card file is built in a **RAM image** (at `0xCC26A2D4`): `INFO`, `DID `, `CN11` from `+0x28`, `HC11` at `+0x804`, **`W001` at `+0x848`** and **`W002` at `+0x1DF8`**, each a 4-byte tag, a length and then **56-byte records** (`u16` track, 4 bytes, 50 bytes), room for 99 tracks. `0xAC0E4F7A` stores one track's two 50-byte buffers into those records (a track number in `r14`, the buffers in `r4` and `r5`); `0xAC0E4DBA` counts the used records and the code at `0xAC0E4E40` to `0xAC0E4F70` packs them to the front. **Nothing calls the setter by address or pool word**: it is reached through a dispatch Ghidra does not follow, so where the 50 amplitudes **come from** was not found.
- The program is a set of **named RTOS tasks**, each in a descriptor `entry, priority, stack size, stack address, name` (`0xAC00F95C` is `MDSPBPM`'s, `0xAC00B2D0` is `CDTIWAVE`'s). `MDSPWAVE` (entry `0xAC100FB6`) and `CDTIWAVE` (`0xAC0D4A5C`) **are not the waveform**: they handle disc timing (track numbers capped at 99, frame counts times 75, a ring of 24-byte time records) despite their names. There is also an `MDSPBPM` task, so the BPM counter is a separate DSP-fed task (nothing of it is written to the card).
- **Threshold tables, unproven.** Two groups of ascending tables sit in the data area near the MP3 decoder's error strings. At `0xAC010B60`, twelve tables of 15 values (so 16 levels, the width of a 0 to 15 amplitude), in six variants twice over (steps of 8 up to 64 then 16 to 160, and five wider ones): **[inferred]** these are the six *auto cue level* settings, which would make them silence thresholds, not the waveform. At `0xAC016814`, nine pairs of a 23-value and a 14-value table (log-like, up to 576 and 192): **[inferred]** display level-meter scales. **No code that uses either was found** (no pointer to them exists in the image, so they are reached through a base register), so neither is shown to be the waveform scale.
- **Dead end, and a better route.** Following the producer through function-pointer code is slow, and the amplitudes may come from the DSP, whose code is not in this file (`G11.UPD` is a different image). The reliable way is empirical: a **disc of known test tones** (a staircase of levels, one level per column of a track exactly 50 columns long, plus pulses and noise to tell peak from mean) played on the CDJ, then the player's records read back against the known levels. That needs one CD-RW and the player, and gives the scale exactly. **It was made, burned and played** (2026-11-14 and 15): see
  [wave-test-disc.md](wave-test-disc.md) and "The waveform, measured" below.

### When the player writes what

- **Loading a disc** adds its entry to the index (disc 2 of `09`: an entry, **no file**).
- **A file we write with cues only is taken and then grown** (`t2`, `t3`): the player keeps the cues and the order,
  and rewrites the file with `W001` and `W002` records for the tracks played: slots up to the highest track
  played (12 of each after a jump to track 12, track 0 in a slot not played), or just the two tracks played in `t3`. So a file written by Dubcrate does not stay the file Dubcrate wrote; merging
  must (and does) keep every record it finds.
- A disc's **file is created once a waveform record is first recorded** (played long enough);
  until then the disc has no file. A file with no cues has `CN11` with length 0 and no count.
- **Cues, loops and hot cues** are appended to the file as they are set. Not seen: a file
  created by a cue before any waveform (the owner played the disc each time).
- **BPM is not on the card**: the chunks are only `INFO`, `DID `, `CN11`, `HC11`, `W001`, `W002`,
  `CSUM`, every byte of them is accounted for, and tracks played at length (so the BPM counter
  had run) added nothing but waveform. The firmware's tag table has no BPM chunk either.

### The waveform, measured (2026-11-15)

Two discs of test signals were burned by Dubcrate and played on a CDJ-1000MK3 (firmware 2.20) with a card in, and the
card imaged each time (`wave-test-disc.md`; images and files in `~/cdj-card/`, the scratch analysis in
`~/cdj-card/wave-analysis/` and `wave-analysis2/`, outside the repository). **Disc 1** (20 tracks of tones, steps,
noise and pulses, 34 minutes) is the card's `CDJ00003`; **disc 2** (10 probe tracks, then nine real tracks of the
owner's, 63 minutes) is `CDJ00004`; both were found by their TOC, so **a disc Dubcrate burns gets the ID that
`toc_from_lengths` computes** (gap (d) in "Gaps" is answered). Every track on both has a complete record.

**The structure.** 100 columns of 1/100 of the track (1 s on a 100 s track, 0.5 s on a 50 s one, 3 s on a 300 s one), the audio
of column k starting at k times that length: **no shift**. The low nibble of a `W002` byte is the earlier column (the
nibbles are in file order high first, so a writer must pack time order into them: `WaveRecord.fine_in_time_order`).
Proof: single 125 Hz events of 0.5, 1, 2 and 3 s land in the right nibbles of the right bytes.

**The scale: 2 dB a value, exactly.** A 125 Hz tone (both channels) reads `floor((level + 36) / 2)` for its peak level in dBFS,
capped at 15: -6 dBFS and above 15, -9 13, -12 12, -15 10, -18 9, -21 7, -24 6, -27 4, -30 3. A track that is audible (peak above
about -41 dBFS) never reads under **2**, near silence reads **1**, and a drop of N dB lowers the value by N / 2.

**What it listens to: a bass band, not the whole signal.** A 1 kHz tone reads the floor of 2 at any level from -39 to -3 dBFS; tones at
-12 dBFS read 40 Hz 2, 50 Hz 4, 63 Hz 6, 80 Hz 8, 100 Hz 10, **125 Hz 12, 160 Hz 12**, 200 Hz 11, 250 Hz 9, 315 Hz 7, 400 Hz 5 to 6,
500 Hz 3, which is a band-pass peaking at 125 to 160 Hz falling **12 dB an octave on both sides** (a pair of 2nd-order Butterworth
sections, high-pass about 115 to 140 Hz, low-pass about 150 to 210 Hz). Loud high tones add to a quiet bass tone as the **peaks add**
(125 Hz at -30 reads 3, and 5 with 1 kHz at -6 on top). **Stereo is the mean of the two channels** `(L + R) / 2`: left only reads
3 values below both, an inverted right channel cancels to 1.

**The time behaviour.** A column averages over **roughly the first 0.7 of its length** with a **power mean** (not a peak, not a mean): a
0.5 s event at the start of a column reads 13, a 0.2 s event 10, a 0.05 s event is not seen, and the column after an event reads 1 at once
(no release). Pulse trains read higher the more pulses a column holds and saturate; the **two nibbles of a pulse byte differ by 2**,
and a steady tone in 0.5 s columns alternates by 2 values: the sample instants follow CD frames (1/75 s). Neither is modelled.

**The model** (`src/dubcrate/wave_model.py`, fitted on the nine real tracks of disc 2): the band-pass above on the mean of the channels, a power mean
(exponent 4) over the first 0.7 of the column, in dB, `floor((dB + 45) / 2)`, with the floors of 2 and 1. **Against the player's own
values on real music: 34% exact, 76% within one value, 91% within two, mean error 1.2 values, bias +0.2** (46% exact with an offset of 46, but a bias of +0.6);
**on one new track it was not fitted on: 91% within one, 96% within two, mean error 0.73**; on 20 varied test tracks it is within one
value for 79 to 100% of the columns of every track. It reads **pure tones about one value high**. The model scales a quiet track up
so its loudest column reaches 15 (the owner's decision: a track turned down by level matching is not a thin line), which the player
does not do; `wave_check.py` / `dubcrate card check-waveforms` compares it with any disc the player has played.

**Not understood: the dip after a drop.** After a drop in level of 6 dB or more, a whole 2 s block reads (1, 1): the first block after the
drop for 6, 8 and 12 dB, two blocks for 18 dB; drops of 2 and 4 dB show no dip (the value lags by one column); a rise never dips. Real music
has it too (about one column in a hundred on a few tracks). It is not release time, ringing or a gain recovery. The model does not have it.

**What the player does with a record we write** (`wt1`, the write test: a commercial 20-track CD given our records for four tracks, played):
it **accepts** it (no card error), **draws** it (track 1's sawtooth showed on the screen), **loads the track at once** with no analysis
(a track with no record is analysed on the player during loading, then recorded), and **keeps** it: after play the file's other discs
and the index are byte for byte unchanged, and a track played in part still holds exactly the values we wrote (tracks 1 and 2: 100 of
100 columns identical). A part-written record is completed only in the columns that are zero (track 4: the half we wrote kept, and the
columns the player reached in the other half, 50 to 56 after a scrub to the end, filled). So Sync writes a record only for a track with none.

**What we first got wrong** (so nobody repeats it): the first fit (a peak or RMS of the full-range signal over 100 columns read high
nibble first, `wavefit.py`, now removed) found nothing; then a reading of 50 blocks with two measurements each, from the pairs being read
the wrong way round, fitted 83% but was wrong in structure; the first bass-band fit shifted the columns by 1 s for the same reason.
Disc 2's probes (level steps down and up, a 125 Hz ladder, tone ladders, mixes, stereo, pulses, single events, and a 50 s and a 300 s track)
settled each.

### The first card stopped being detected (unexplained)

After the first `card sync --yes` onto the owner's original 2 GB card (the one every image above came from), **neither CDJ registered the card at all** (no card error: no card), though it mounted and read fine on the Mac
(`diskutil verifyVolume`: clean, exit 0; the files and the 144-byte, five-entry index were well formed). A full
restore of `t3-new-disc` onto it (the image the player had read an hour before) and a format on the Mac did
not bring it back. A **second card, the same brand and size**, was registered at once, was started off by the
player (CD 1 in its index) and then took the same write and showed every cue.

Not explained. Suspects, none shown: the card or its adapter wore out or was damaged (a full-size SD card pulled
mid-write can be left with a bad flash controller that a Mac still reads); the first write was done **before
`write_card` flushed the card** (`os.sync()`, added after) and the owner may have pulled the card without
ejecting it (not confirmed). The second write was flushed and ejected properly. **Rule since: eject the card
through the system before pulling it**; the command and the tab say so. If a second card ever stops being
detected after one of our writes, stop and treat the write as the cause.

## The code

`src/dubcrate/cdj_card.py` (Qt-free, no I/O beyond `read_card`) parses and writes both files:
`DiscId.from_toc` / `toc_from_lengths`, `IndexFile` (`parse`, `to_bytes`, `find`, `add`,
`next_name`), `TrackFile` (`parse`, `to_bytes`, `new`), `CueEntry`, `HotCue`, `WaveRecord`,
`checksum`, `read_card` and `CardFormatError` (an expected error: plain words). Its tests
(`tests/test_cdj_card.py`, real files in `tests/data/cdj_card/`) check:

- every file the player wrote **round-trips byte for byte**;
- every ID is reproduced from its disc's real TOC;
- a file **built from nothing equals the player's own** one-cue file and waveform-only file,
  checksum included.

So the format is understood well enough to write. **Shown on 2026-11-14** (firmware 2.20): a CDJ-1000MK3 accepts files
written here (the experiments table's `t1` to `t4`), and the Sync tab and `dubcrate card sync` use the module.

## Gaps, and where more testing is indicated

Roughly in the order I would do them. Each row is a gap, why it matters, and the test.

### 1. Does the player accept a file we write? (the one that matters)

**(c) is answered too, 2026-11-14: yes** (`t3`: a fourth disc, new to the card, given an index entry and a file by our writer: its cues and loop showed at the right places). **(b) is answered too, 2026-11-14: yes** (`t2`: a disc with an index entry and no file, given a cues-only file with
no waveform: the cues showed, and the player then grew the file to hold waveform slots for every track and kept
our cues as they were; see the table). Still open: **(d) a disc Dubcrate burned** (which also checks `toc_from_lengths`): the burn window now checks it by itself (`burn_check.py`: it reads the burned disc's TOC and says in the log whether it matches the plan), so the next real audio burn on a Mac answers it. The whole path run by `dubcrate card sync` itself was done (`t4`). **(a) is answered, 2026-11-14 (firmware 2.20): yes.** A CDJ-1000MK3 loaded `CDJ00001.PDJ` as written by
`cdj_card.py` with its cues replaced: every memory cue and the loop showed, there was no `CARD ERROR` or
`UNFORMAT`, and the file was left byte for byte as written. (The image's files were macOS-dated and had macOS's
`._` sidecar files beside them, and the player ignored all that, so that confound is out for (a).) (b), (c) and
(d) below are still open. Until (b) and (d) are answered too, "a CDJ loads our files" is shown for an existing
file only.

Everything before 2026-11-14 was reading: the writer was a hypothesis that happens to reproduce the player's bytes.

- **Test, least risky first**, always on a *copy* of an image, written back to the card with
  `dd` (so the card only ever sees exact bytes and no Spotlight files; writing into the image file
  without mounting it, e.g. with mtools at the partition's offset, would also keep them out: not
  tried), with the baseline images kept: (a) swap the cues in a disc's *existing* file (`CDJ00001.PDJ`) for others and see them
  on the disc; (b) give a disc that has an index entry but no file (CD 2 or 3 as in `09`) a file
  of cues only, **no waveform**; (c) make a disc that is not on the card yet: an index entry plus
  a file, from its TOC; (d) the same for a disc **Dubcrate burned**. A wrong checksum, order or
  length may show as the player ignoring the file, `CARD ERROR` or `UNFORMAT`; a card the player
  rejects can be reformatted in the player, and the images restore it.
- Also try: leaving out the waveform records, zero versus junk in the 2 checksum pad bytes, the
  flags word `0x0020` versus 0 in the file, and an index entry whose file is missing.
- **Tooling gap:** nothing yet writes into an image or restores one to a card
  (`spikes/cdj_card_snap.sh` only reads).

### 2. The disc ID outside the discs seen

Three discs of 12, 12 and 20 tracks, one session each.

- A **one-track** disc has no track 2 (`DiscId.from_toc` refuses it, on purpose); a **two-track**
  disc (track 2 is also the last) is allowed but not seen. Test both: load one, read the index.
- **Collisions**: the ID is three numbers and a count, so two different discs with the same track
  lengths share an entry, and so their cues. Unlikely for real albums, certain for two copies of
  the same Dubcrate disc, which is probably what one wants.
- Not tested: more than one **session** or an enhanced disc (CD-Extra, mixed mode), a first track
  starting after 150 (hidden pregap audio), the 99-track limit, an **MP3 disc** (the firmware has
  a separate ID type, 3, whose input is not known) and CD-R versus pressed CDs (none was a Dubcrate
  burn; which they were was not noted).
- **Dubcrate's own burns**: `toc_from_lengths` is a guess at the TOC a burn gives (track 1 at
  150, then each track's whole sectors plus `gap`). **Burn a disc, run `drutil toc`, compare** for
  a gapless disc and one with a gap, with both burn backends (DiscRecording, and `cdrdao` where
  it can be tried). The first track's start with a non-zero gap, and whether
  `pregap_sectors` shifts it, is not known.

### 3. Fields whose meaning is not known

Each is a constant in what was seen, written back verbatim by the code, not interpreted.

| Field | Seen | To find out |
|---|---|---|
| `INFO` `u32` | 2 | try other cards, firmware versions, a fuller card |
| `HC11` `u32` | 0 (no hot cue), 60 (A, or A and B) | all three set; set then cleared |
| ID flags word | `0x0000` index, `0x0020` file | load a disc the player has seen before; a deleted disc |
| `CN11` last `u32` | 0 cue, `0x00010000` loop | loops of other lengths, other tracks, loop on/off (reloop) state |
| `HC11` kind, end, extra, tail | `0x10`, 0, 0, 0 | a hot cue set while looping; hot cue C |
| waveform reserved bytes | 0 | none seen non-zero |
| index `u32` after `INDX` | 1 | nothing to vary yet |
| a loop's end in a hot slot | not seen | set a loop, then a hot cue on it |

Not on the card (so not a gap): BPM and key, cue colours and labels (no field exists), the
disc's name.

### 4. Behaviours not tried

- **Deleting**: a cue, a hot cue, all cues of a disc, a whole disc from the card (the firmware has
  `DISC DELETE?`). Do the index entry, file, indices and `CN11` shrink or renumber? Is a gap left?
  **File numbering** after a delete is unknown, so `IndexFile.next_name` (highest + 1) is a guess.
- **Order**: the file keeps cues as set; whether the player sorts for display, and whether it needs
  them sorted on loading, is not known. Cues are written here in the order given.
- **Limits**: the manual says 100 cue and loop points per disc (`MAX_CUES`); what the player does
  at 101, and when the card is full (`MEMORY - CARD FULL`), was not tried. Cues past a track's
  end, a wrong track number, or two cues at one position were not tried.
- **Setting a cue before any waveform exists**, so a file is made by a cue alone.
- **When the player writes**: images were taken after the card was out, so how long after a
  change the file is complete, and whether pulling the card early corrupts it, is unknown (no
  corruption was seen in the images taken).
- **Cue precision**: the owner set cues to the exact frame only for 0-frame values (5 s, 10 s...);
  how a cue set by ear is quantised was seen once (the loop), and agrees with a frame count.

### 5. The waveform

Solved enough to write (see "The waveform, measured" and `card-sync.md`, "Waveform sync"). Open: the **dip after a drop** and the
two-nibble difference on pulses (not modelled), the model's accuracy on music **very different from the nine tracks it was fitted on**
(run `dubcrate card check-waveforms` after playing a new disc), **MP3 discs** (first seen 2026-11-23, see section 6; one track played, no cue yet),
and **firmware other than 2.20**.

### 6. Other media and the rest of the card

- **MP3 discs** (`PIONEER/MPJ/`): **first look 2026-11-23, [hardware]**, one MP3 disc and one track played. The
  real files are in `tests/data/cdj_card/mp3/` (the player's own, untouched). The disc was burned on Linux by
  Dubcrate (15 files: 3 in the root, 4 in each of folders A, B and C; an ISO of **84,029** sectors), and the owner
  played "Gotta Get Thru This", the first track of folder A, to the end, with no cue set. The player made
  `PIONEER/MPJ/MPJINDX0.PDJ` (48 bytes) and `MPJ00001.PDJ` (928 bytes) and nothing else; their times are 1980-01-01
  (the player has no clock), as for the audio files it writes. **The player's waveform for an MP3 loads at about
  real-time speed** (owner), so a track's waveform is only there after it has been played through: the same gap
  that writing waveforms in Sync closes for audio discs.
  - **Index**: `PIONEER INDX` `00000001`, then the media word `MP3 ` and a `u32` (1: the disc count, as `CD  ` and
    `00000019` = 25 on the audio index), a 0x18-byte header exactly as documented above; then **24-byte entries, the same
    as an audio index's**: the file name `MPJ00001` (8 ASCII bytes) and a **16-byte disc ID**. No checksum: 24 + 24 = 48.
  - **The disc ID is not a TOC.** Read as an audio ID it is nonsense. `05 2c 06 0f 33 87 45 e7 01 39 75 01 48 3d 00 00`:
    **bytes 8 to 10 (`01 39 75` = 80,245) are the start sector of the last file** on the image (`/D003/004.MP3`), and
    **bytes 11 to 13 (`01 48 3d` = 84,029) are the image's size in sectors** (the ISO's volume size, also what `cdrdao`
    wrote). So, as for an audio disc, the ID comes from the numbers of the disc itself, here the ISO's. **[inferred]**
    Byte 3 is `0x0f` = 15, the number of files; the rest (`05 2c 06`, the four bytes `33 87 45 e7`, and `00 00`) is
    **[unknown]**: a hash or counts of folders and entries. One disc cannot say; a second image with known numbers can.
  - **Track file** (second look, same day, after the owner played one track from the root and one from folder B;
    `MPJ00001_three_tracks.PDJ`, the first state is `MPJ00001.PDJ`): **one file for the whole disc**. Tracks from the
    root, folder A and folder B all went into `MPJ00001`; there is no `MPJ00002` and the index did not change. It is
    the audio file's layout: `PIONEER INFO` `00000002`, `DID ` `00000010` and the 16-byte ID (the index's), `CN11` with
    length 0 (no cues; with cues it is `2 + n x entry` as for audio: the entry's size here is **[unknown]**, cdjmerge
    says 132), `HC11` with length 0 then a fixed block of **396 bytes = 3 x 132** (an audio file's is 60 = 3 x 20: the
    hot cue entry is 132 bytes on MP3), `W001`, `W002`, and `CSUM` `00000002` + `u16` + 2 pad bytes. **The audio
    checksum algorithm (`cdj_card.checksum`) validates both files** (0x12ac, then 0x4f53). The chunk lengths are the
    records' bytes exactly, and **a record is 56 bytes as on audio: `[3 x u16: folder, file, 0][50 data bytes]`**
    (audio: `[u16 track][4 zero bytes][50]`, so the first u16 is "folder" here and the track number there).
  - **A record is keyed by (folder, file)**, measured: root is folder 0, folder A is 1, folder B is 2; files count from 1
    inside their folder, in the image's order. After the owner's first session the file held (0,1) with 15 of 50
    columns, (0,2) with 4, (1,1) full (the track played to the end), (1,2) with 1 (the player started the next track of
    the folder when the first ended, and it was stopped at once); after the second: (0,2) had grown to 49 columns (the
    track played now, same start), (1,1) unchanged, and **(2,1) was new and full**. So a waveform is filled in from the
    start as the track plays (columns not yet played are 0, as on audio), `W002` gets the same keys and the fine
    columns, and the file grows by a record when a track not in it is first played. **Only the records of played tracks
    exist** (no root file 3, no folder A files 3 and 4). **Third look (`MPJ00001_seven_records.PDJ`): they are appended in
    the order the tracks were first touched, not sorted**: after the owner played root file 3 (Summertime Blues, to the
    end) and folder C's last file (Safe and Sound, stopped about a minute from the end), the file had gained (2,2) with
    **0 columns** (the player started the next track of folder B when Rather Be ended: a track that only starts gets a
    record) and then (0,3) with 49 columns, so (0,3) sits **after** (2,2). The index was unchanged and the checksum still
    validates. **Safe and Sound, (3,4), has no record at all**, although most of it was played: either a record is only
    written when the player moves on (the track ends, another is loaded, or the disc is ejected) and this one was not
    flushed before the card came out, or something else: **[unknown]**. The owner **paused it, waited a couple of
    seconds and pulled the card** (no other action). That rules out "only when played to the end" (records for tracks
    stopped at 0, 1, 4 and 15 of 50 columns exist, and the audio notes have a part-filled record after 2 seconds of
    play); what is left is a flush when the player **leaves** a track (it ends, another is loaded, the disc is
    ejected), which every record present fits and a pause does not trigger. Two tests would settle it: play a new
    track for 20 s then load another and pull the card (does the first have a record?), and play one and pause and
    wait longer before pulling. It does not matter for pre-writing waveforms (below), but it does for the safe-eject
    advice, and for reading back cues set on the player.
  - **Our waveform model against the player's own MP3 records** (`dubcrate`'s `library.waves.analyse`, run on the
    exported copies of the burned files, against the `W002` columns in time order, 2026-11-23): Gotta Get Thru This 67%
    of 100 columns within one value (23% exact), Rather Be 63%, Sorry For Party Rockin' 78%, Summertime Blues 90% (64%
    exact): **about 74% within one, as for audio discs (76%)**, so the same model serves MP3 discs. Not yet checked:
    that the player keeps a record we write for an MP3 as it keeps one on an audio disc (it never re-measures a column
    that has a value there).
  - **The disc has `HC11` of 396 bytes, so hot cues A, B, C are 132-byte entries**, and a played track with no cue has
    all of it zero. `Dubcrate`'s audio parser still refuses the file (it reads a 60-byte `HC11` block and takes the first `u16`
    of a waveform record as a track number); the layout is otherwise the same code path.
  - **Next, in this order**: find out when the player writes a record (Safe and Sound is missing); set one memory cue and one hot cue on a played track (the 132-byte entry layout); a loop;
    then a second image to pull the rest of the ID apart (`05 2c 06`, `33 87 45 e7`, `00 00`). Then `card_sync` could
    write MP3 waveforms and cues: **no code reads or writes `MPJ` yet.**
- **`INTERNAL MEMORY` / `CARD MEMORY`, `CARDCOPY_MODE`, `CUE FOLDER00`, `.BAK`, `THUM`** appear in
  the firmware and not on the card seen: the player can copy cues between its internal memory
  and the card, there may be several folders of cues, and `.BAK` may be a backup written
  somewhere. A copy to the card and a second folder were never made.
- **Firmware version and hardware**: **both of the owner's players are on 2.20** (read off the display by
  holding RELOOP/EXIT for 10 seconds with the player on, 2026-11-14), the version disassembled here (build
  20061021). Pioneer also lists a **2.30** update that these players do not have; nothing here has been
  seen on it, and a different version or a CDJ-1000MK2 may lay the file out differently.
- **Cards**: one 2 GB card, formatted by the player. SDHC and larger sizes, cards formatted
  elsewhere (cdjmerge says the player will not accept them), and a card with other files on it
  were not tried. `dubcrate card format` (2026-11-15) makes a card on a Mac; **the player has not seen one**
  (`card-sync.md`, "Formatting a card"). `00-blank.img`'s layout: MBR, one type-0x06 partition from sector 32,
  3,932,128 sectors, 64 sectors per cluster, 8 reserved, 2 FATs, 512 root entries, label `DISK_IMG`.

### 7. What the sample is

One owner, one player, one card, three CDs: 6 cues, 2 hot cues and 1 loop,
all on one disc. The decoded fields are consistent with each other and with the firmware's strings, but
nothing about a rarely used field (a second loop, hot cue C) is more than a guess until seen.
Add each new thing to the table of experiments above, with its image name.

## Reproducing the analysis

(2026-11-14: Ghidra is installed as a Homebrew *formula*, so `analyzeHeadless` is at
`/opt/homebrew/Cellar/ghidra/12.2/libexec/support/analyzeHeadless`; `spikes/ghidra/RefsTo.java ADDR...`
lists every reference Ghidra knows to an address, calls and data. The import with `SeedFunctions.java` takes
a few minutes. A script's output is the lines containing `<ScriptName>.java>` in the headless log.)

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
