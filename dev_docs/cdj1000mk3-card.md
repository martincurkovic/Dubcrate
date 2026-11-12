# CDJ-1000MK3 memory card: what is known

The goal is to pre-cache cue points, hot cues and (if it can be done) waveforms onto the SD
card in a CDJ-1000MK3, so a burned disc loads with them already set. The card format is
Pioneer's own and undocumented. This file records what is known, tagged like
[burning.md](burning.md): **[measured DATE]** from a file we have, **[notes]** from someone
else's notes and not checked, **[inferred]** a reading of the evidence that is likely but
not proven, **[unknown]**. Nothing here has been checked against a real card yet.

The rekordbox formats (`export.pdb`, ANLZ) do **not** apply: this player predates rekordbox
export. See [rekordbox-analysis.md](rekordbox-analysis.md) for those.

## Prior work

- **[notes]** [rdingwall/cdjmerge](https://github.com/rdingwall/cdjmerge) and its
  [wiki](https://github.com/rdingwall/cdjmerge/wiki/CDJ-1000MK3-SD-Card-File-Format-Info)
  (README May 2011, wiki 2015, no code). Hex-editor work on MP3 discs only, by its own
  account "probably contains mistakes". It gives: data in `PIONEER\MPJ\`; an index file
  `MPJINDX0.PDJ` with a 16-byte header `PIONEER INDX....MP3 ..`; one `MPJ0xxxx.PDJ` per
  track; a 14-byte unique ID in both; the ASCII `DISC`; a trailing `CSUM` plus 8 bytes.
  Cue, loop and wave encodings are not documented. The card must be formatted by a camera
  or the player, not by Windows or the SD Association's tool.
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
| `G11.UPD` (223 KB) | `CDJ1K3  DVPL0120`, inner name `G11.mot` | `27ee202f...4c1780` | Decodes to 128 KB (`0x000000`-`0x021000`) of high-entropy data. **[unknown]** what it is: encrypted, compressed or a bitstream. Not the card code. |

The program is shared with Pioneer's DVJ-X1 (strings say "Good Good Yamada! Bravo Bravo
DVJ!" and `DVJ-X1`), which is why the card code has `DVJ` as well as `CDJ` and `MPJ`.

### The `.UPD` transport

**[measured 2026-11-12]** Decoded fully; `spikes/cdj_upd_decode.py` does it. After an
18-byte header line:

- Records start with `0x1f`, a tag (`10`, `18`, `2b`, or `22 54`), then a 3-byte address
  (steps of 16), 16 data bytes and 1 check byte. 61,012 data records in the main file,
  and every one decodes to exactly 20 bytes, with no two records disagreeing about an
  address.
- Every byte is stored as `plain + 0x40` (mod 256). A stored byte under `0x40` would
  clash with the framing, so it is sent as two bytes: `0x3a + high nibble`, then
  `0x30 + low nibble`.
- The check byte is **[unknown]**: it is not a plain sum of the address and data. It
  doesn't matter for reading, only for writing a modified update (which we won't do).
- Two records at the end are not data (`28`: `04 00 00 00 bb`, and an empty `1a`).

### The image

**[measured 2026-11-12]** The main program decodes to 26 segments between `0x020100` and
`0x1bf680`, about 976 KB of the 1.6 MB span; the big one is `0x020800`-`0x031ce0`, then
others. The code is big-endian **SuperH**: functions start `sts.l pr,@-r15` and calls look
like `bsr` plus `nop`. **[inferred]** SH-2 versus SH-4 is not settled: SH-2, SH-4 and SH-4
with the FPU all decode about 61 to 65% of the code segment (a halfword at a time, data
included), so that test does not tell them apart.
The runtime is a µITRON-style RTOS (task and message-buffer names such as `dly_tsk`,
`wai_sem`, `rcv_mbf`). It is a flat image with no symbols; load it at `0x020100` in a
disassembler such as Ghidra. Whether the load address is where the code runs is
**[unknown]**; the first four small segments may be vectors or data.

The firmware has **debug strings and state names left in**, so function and mode names can be
found by searching strings.

## The card format, from the firmware

All **[measured 2026-11-12]** unless marked. Addresses are in the decoded image.

### Names

At `0x02ec20`, a table of space-padded names: `PIONEER`, `DVJ`, `CDJ`, `MPJ`, `MPJINDX`,
`THUM`, `DVJINDX`, `CDJINDX`, `.PDJ`, `.*`, `.BAK`. So there are **three media kinds**, each
with its own index and track files (`DVJINDX`, `CDJINDX`, `MPJINDX` plus the number, and
`DVJ`/`CDJ`/`MPJ` plus the number), all `.PDJ`, and a `.BAK` for backups. cdjmerge only saw
`MPJ` (MP3 discs). The audio-CD files, which are the ones Dubcrate cares most about, would
be `CDJINDX0.PDJ` and `CDJ0xxxx.PDJ` **[inferred]**. `THUM` is probably DVD thumbnails.

### Chunk tags

At `0x02ea84`, a list of 4-character tags, each followed by a zero and padded:

```
PIONEER   INDX  INFO  CDJ  MP3   THUM  DID   CN11  HC11  W001  W002  CSUM
```

then `CD`, `MPJ`, `PIONEER CDJ-1000` and `DVJ-X1  TEST...` (a test fixture). **[inferred]**
These are the section IDs in the `.PDJ` files, which matches what cdjmerge found
(`PIONEER INDX`, the media word `MP3`, `CSUM`):

| Tag | Likely meaning |
|---|---|
| `PIONEER INDX` | index file header |
| `INFO` | per-disc or per-track info |
| `DID ` | **disc ID**, the thing that ties a card entry to a disc |
| `CN11` | cue or loop points (the "11" may be a version) |
| `HC11` | hot cues (the "H" and "C") |
| `W001`, `W002` | wave data (the waveform display), two kinds or two resolutions |
| `CSUM` | checksum |

None of the layouts behind them is read yet.

### What else the program shows

- Tasks: `Card Navi`, `Card Receive`, `Card Sprt`, `Card Capture`, `CDCUEREC`,
  `MDSPWAVE`. Modes: `MG_MAIN_CUE_SAVE_MODE`, `HOTCUE_REC_MODE`, `HOTCUE_REC_TIME`,
  `CARDCOPY_MODE`, `CARDIN`, `CARD_CAMEBACK_A/B/C`, `CUE_ROAD_ISYTYPE`, `ROAD_CUE`,
  `TEXT_WAVE_MODE`. Disc kinds: `CdDiscInfo`, `Mp3DiscInfo`, `CdOldDiscInfo`,
  `Mp3OldDiscInfo`, `DISC_KIND`, `DISC DELETE?`. These are the entry points for the
  save, load and disc identification code.
- Messages: `MEMORY - CARD FULL / PROTECTED / NO CARD / UNFORMAT / CARD ERROR`,
  `CUE FOLDER00`, `HOT CUE FOLDER00`, `INTERNAL MEMORY`, `CARD MEMORY`, `Disc Type: MP3`,
  `Disc Name:`. "Old" disc info suggests an earlier ID scheme is still read.
- A run of `0xbf` bytes follows many strings and tags (raw `0xff` in the file). It may be
  real fill or a quirk of the transport; the decode is otherwise consistent, so it was
  left as decoded.

## Still unknown

1. **The disc ID** (`DID `): what it is computed from (TOC, track offsets, a hash) and how.
   It is the blocker for pre-caching, because the card entry must be found by the player.
2. The layouts of `INFO`, `CN11`, `HC11`, `W001`, `W002`: field sizes, time units
   (cdjmerge guesses 1/75 s), cue colours and flags, hot cue slots.
3. The `CSUM` algorithm and what it covers.
4. Whether waveform data can be generated by Dubcrate or only captured from the player.
5. Audio-CD (`CDJ`) specifics, since all earlier notes are MP3 only.
6. What `G11.UPD` is.

## Plan for when the player is available

1. Format a card in the player (not on the Mac). Copy `PIONEER/` off it as a baseline
   (read-only: mount the card read-only and copy, never edit in place).
2. Burn a known audio disc, set exactly one thing, copy the card again and diff. Do this
   for: nothing but loading the disc, one memory cue, one hot cue A, a loop. Change one
   value at a time (a cue 1 s later) to find the time unit and byte order.
3. Use the firmware to explain what the diffs show: search the image for the tags above,
   find their uses (xrefs), and read the disc ID and `CSUM` code.
4. Write a pure parser in the package (tests with a captured card), then a writer, and
   test the writer by loading the card in the player.

## Reproducing the analysis

```sh
mkdir -p /tmp/cdj-fw && cp ~/Downloads/CDJ1000MK3_061023.EXE /tmp/cdj-fw/
cd /tmp/cdj-fw && 7zz x CDJ1000MK3_061023.EXE            # CDJ1K3MN.UPD, G11.UPD
uv run --project ~/Development/Dubcrate \
    ~/Development/Dubcrate/spikes/cdj_upd_decode.py CDJ1K3MN.UPD -o main.bin
strings -n 6 main.bin | less                            # load main.bin at 0x020100
```

Keep the firmware and anything decoded from it **out of the repository** (it is Pioneer's
copyright; this file records findings in our own words). Reading it to learn the card
format is interoperability research, but check your local rules before you publish.
