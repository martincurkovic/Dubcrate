# Card sync: the plan

Goal: a **Sync** tab that writes a Dubcrate disc's cues onto the CDJ-1000MK3's SD card, so the
disc loads in the player with its rekordbox cues already set. The card format is in
[cdj1000mk3-card.md](cdj1000mk3-card.md) (read that first: what is measured, and its
**"Gaps, and where more testing is indicated"**). The module that reads and writes it is
`src/dubcrate/cdj_card.py`. **Status 2026-11-13: Phases 1 and 2's core are built and tested; the Sync tab is not; nothing has been tried
on a player.** Built: `cdj_card.py` (the format, `write_card`, `backup_card`), `card_sync.py` (cue
mapping, merge, plan), `card_discs.py` (a Dubcrate disc's TOC and rekordbox cues), `card_report.py`,
`cli_card.py` (`dubcrate card show | backup | sync`, a dry run unless `--yes`), and the Phase 0 tooling
(`spikes/cdj_card_testimage.py`, `cdj_card_restore.sh`, with two test images made). No file written by
any of it has been loaded by a player.

## Decisions (the owner, 2026-11-13)

1. **Every rekordbox cue becomes a CDJ memory cue**: memory cues, hot cues (A to H) and loops
   (as memory loops). Hot cue slots A, B and C on the card are **not written and never touched**:
   a disc has only three of them (inferred: see below), so there is no room for rekordbox's.
2. **Sync lists only Dubcrate's own audio discs.** Not MP3 discs (the card format for them is not
   measured) and not discs from elsewhere.
3. **Merge, never replace.** Cues already on the card (set on the player) are kept; ours are added
   around them, skipping what is already there.
4. **Tests on the player come first**, then the pure core and a command-line tool, then the tab.
5. **Waveform pre-writing is a later, separate phase**: it needs the amplitude scale
   ([below](#phase-4-waveform-pre-writing)). Sync works without it.

## What Sync does

```
rekordbox cues (ANLZ) ─┐
Dubcrate disc ─────────┼─► plan (pure) ─► compare with the card ─► you confirm ─► back up
disc TOC ──────────────┘                                                              │
   (computed from the disc's tracks, or read from the disc in the drive)               ▼
                                                              write ─► read back and verify ─► eject
```

### Mapping a cue

- Position on the card = **the track's start in the disc's TOC + the cue's time**, in absolute CD
  frames (1/75 s): `start + round(seconds x 75)`. Track 1 starts at 150.
- A loop keeps its start and end. A loop with no length, or an end before its start, is skipped.
- A cue past the end of its track as rendered is skipped and reported.
- Colours, labels and comments are dropped: the card has no field for them.
- **Only tracks with rekordbox cues give any**: a track from the Explorer or a plain folder has
  none unless it is the same file as a rekordbox track (`Library.rekordbox_twin`).

### Merging into what is on the card

- Existing cues are never changed, reordered or removed. New ones are appended in disc order
  (track, then time), with indices continuing from the last (the file keeps the order set).
- A cue on the card at the same track and within **2 frames** (27 ms) of ours counts as the same
  and is skipped. (2 is a starting guess: the player's own cues are to the frame, rekordbox's are
  milliseconds, so rounding can differ by one.)
- **100 cues and loops per disc** is the player's limit. When ours do not all fit, what fits goes
  in, in disc order, and the rest is **reported, never dropped silently**.
- The waveform records, and any hot cues set on the player, stay exactly as they are.
- A disc with no entry on the card gets an entry (the index, then its file). A disc whose file
  does not exist yet (the player makes it when a waveform is first recorded) gets a new one with
  our cues and no waveform.

### The disc's ID, and when it can be trusted

The ID is the TOC: track count, lead-out, track 2's start and the last track's start
(`cdj_card.DiscId.from_toc`). Two ways to get it:

- **Computed** from the disc's tracks (`cdj_card.toc_from_lengths`). Only allowed when **every track
  has a measured length** (`Track.cd_sectors`, written back by a burn): a conservative estimate
  would give an ID the disc never had. Guess: **not yet compared with a burned disc's real TOC**.
- **Read from the disc in the drive** (`drutil toc` on a Mac): exact, and the safe choice for a
  disc that was edited after it was burned (a disc stays editable, and there is no snapshot of what
  was burned, so the plan can differ from the physical disc).

Two different discs with the same track lengths share an ID and so their cues.

### Writing safely

- **Never write without confirmation**, like a burn: a list of what will change per disc, then the
  owner agrees (CLI: `--yes`).
- Copy `PIONEER/CDJ` aside first (kilobytes, no `sudo`), under Dubcrate's data folder with a
  timestamp. Touch nothing outside `PIONEER/CDJ`.
- Write each disc's file before the index (an index entry without a file is a state the player
  makes itself), through a temporary name and a rename, then **read everything back** and check the
  checksums.
- Refuse a volume that is not a card: it needs `PIONEER/CDJ/CDJINDX0.PDJ`, or to be empty and
  FAT16. **A card must be formatted by the player**; creating `PIONEER/CDJ` on an empty card is a
  test not yet made.
- Offer to eject afterwards. macOS writes `.Spotlight-V100` and `.fseventsd` on a card it mounts;
  the player ignored them, but the write test should confirm that holds for files we write too.

## The pieces

| Piece | Where | Needs |
|---|---|---|
| Card files, disc ID | `src/dubcrate/cdj_card.py` | **done** (read, write, round trip) |
| rekordbox cues to card entries, merge, plan | `src/dubcrate/card_sync.py` (**done**, pure, no Qt; 24 tests on the real card files) | cue parsing checked on real rekordbox cues |
| Backup, write, verify, eject | same, with a thin I/O layer | the write test |
| Command line | `dubcrate card show / backup / sync DISC... --card PATH [--dry-run] [--yes]` | the above |
| Sync tab | `ui/`: a tab **pinned at the bottom** of `source_tabs.py`'s strip (it has Library, Explorer and pins now, all added "at the bottom": a fixed bottom group needs a small change), a middle pane with the discs and the card | the above |
| TOC from the drive | the burn backends (`media_info` already asks the drive about its disc) | `drutil toc` equivalent in DiscRecording |

The tab: a card picker (mounted volumes that look like a card), then the Dubcrate audio discs with a
tick box, track count, **cues found in rekordbox**, and a status per disc: not on the card, on the
card and in sync, differs (cues to add), cues on the card that are not ours (kept), or skipped with
the reason (unmeasured lengths, one track, MP3). Buttons: Sync Selected, Sync All, Show Card, Back
Up, and a dry-run list before anything is written. Tests use a temporary folder as the card.

## Order of work

### Phase 0: on the hardware (the owner; about half an hour, none of it destructive)

Do these in order; each is an image in `~/cdj-card/` and a row in the doc's experiments table.

1. **Note the player's firmware version** (its display shows it at start-up) in AGENTS.md's hardware
   notes. The disassembly was 2.20.
2. **Write test** (gaps section 1a, 1b). Two test images are **already made** in `~/cdj-card/` (by
   `spikes/cdj_card_testimage.py`, from copies; the originals are untouched). Run these in a
   normal Terminal (they need `sudo`), with the card in its reader, then in the player:

   | Image | What it holds | Expect on the player |
   |---|---|---|
   | `t1-cues-swapped-cd1` | CD 1's cues **replaced**: memory cues at 20 s and 30 s into track 1, 45 s into track 2, 10 s into track 3, a loop 50 s to 52 s in track 1. Hot cues A (track 1, 15 s) and B (track 3, 2 s) and the three waveforms are kept. | Load CD 1: the new cues, not the old ones (2, 5, 10, 4 s, 1:04, the 40 s loop) |
   | `t2-cd2-file-from-scratch` | CD 2 given a file **made from nothing**, cues only (no waveform): 15 s into track 1, 20 s into track 2, 30 s into the **last** track (12). | Load CD 2: those three cues |

   ```sh
   spikes/cdj_card_restore.sh t1-cues-swapped-cd1 /dev/diskN     # N from: diskutil list external physical
   ```
   Put the card in the player, load CD 1, find the cues (the memory call buttons), play a track,
   eject the disc, pull the card, then image it: `spikes/cdj_card_snap.sh 11-after-t1 /dev/diskN`.
   Then `uv run spikes/cdj_card_dump.py diff ~/cdj-card/t1-cues-swapped-cd1.img ...` (mount each
   read-only first) or just dump `11-after-t1` to see whether the player **kept, changed or
   dropped** what we wrote. Repeat with `t2-cd2-file-from-scratch` and CD 2 (`12-after-t2`).
   **Outcomes**: the cues appear (the writer is accepted: this is the point of Phase 0); the player
   says `CARD ERROR` or `UNFORMAT` (reformat in the player, restore an image, and tell me what
   was on the display); the card loads but shows none of our cues (the file was ignored: the ID,
   the chunk layout or a directory entry is wrong: image it and I compare). To put the card back
   to how it was: `cdj_card_restore.sh 10-third-cd-20-tracks /dev/diskN`.
   **A confound to remember**: the files macOS wrote into the test images carry today's date, the
   player's carry 1 Jan 1970, and macOS added a `.Spotlight-V100` folder. If a file is ignored,
   that is one suspect (the content is the other), and the way to rule it out is writing the same
   bytes into the image without mounting it (not built).
3. **rekordbox cues**: set a handful in rekordbox on a few tracks of one disc's tracks (memory
   cues, hot cues, a loop; be exact: a cue at exactly 1:00.000), so the cue parsing can be checked
   against real files. (None of the 300 sampled tracks had any.) Dubcrate only reads them.
4. **A burned disc's TOC**: burn a small Dubcrate disc, `drutil toc` it, save the output, so
   `toc_from_lengths` is checked.
5. Optional, as no longer blocking: set hot cue A on a second track while A exists on a first,
   to confirm there are only three hot cue slots per disc.
6. For the waveform, **copy the audio** of the played tracks off the CDs (macOS mounts an audio CD
   as AIFF files): CD 1 tracks 1 to 3, and track 1 of CD 2 and of CD 3. Kept locally, never added.

### Phase 1: the pure core and the command line: **built**

`card_sync.py` (cue mapping, merge, plan), `card_discs.py` (the library half), `card_report.py`, and
`dubcrate card show | backup | sync`, with tests on the real card files (`tests/test_card_sync.py`,
`test_card_discs.py`, `test_cli_card.py`). Nothing here is trustworthy until Phase 0 step 2 says the
player takes our file, and the rekordbox cue reading is checked on real cues (step 3).

### Phase 2: writing: **the core is built; what is left**

`card sync --yes` already backs up, writes (files, then the index), and reads back. Still to do:
eject afterwards, a card with no `PIONEER/CDJ` yet (`write_card(create=True)` exists and is **untried on
a player**), reading the TOC from the disc in the drive instead of computing it, and whatever the write
test shows the player needs.

### Phase 3: the Sync tab

The pinned tab, the status column, dry run and confirmation, the user guide page and its
screenshots (`scripts/make_screenshots.py`), `manual.md`.

### Phase 4: waveform pre-writing

Reading is done: a record is 50 amplitudes (0 to 14) and 100 nibbles of the same, filled as the
track plays (cdj1000mk3-card.md). Writing needs what a column means and the scale.

- **Fit it from data we already have**: five tracks with a full record on the card, 250 coarse and
  500 fine values, against the audio of those tracks (step 6). Try peak and RMS per column, linear
  and dB, columns as equal fractions of the track. Check by predicting a held-out track.
- A **firmware decompile** is the cross-check, not the start: the player has an `MDSPWAVE` task, so
  a DSP may do the metering and the firmware only the final quantising. Ghidra is not installed and
  the firmware extract is gone: re-extract (`dev_docs/cdj1000mk3-card.md`, "Reproducing the
  analysis") before trying.
- If the fit is not good enough, **one rewritable disc** of test tones at known levels covers
  every level; not a stack of CD-Rs.
- Then write full records ahead of time and test that the player accepts a complete one.

## Risks, in the order they would hurt

1. **The player may not accept a written file.** Phase 0 step 2 decides it; everything is on
   hold until then. A rejected card is reformatted in the player and restored from images.
2. **rekordbox cue parsing is unverified** (fake data only), and so is whether a cue's time lines
   up with the rendered audio: an MP3's encoder delay can shift the player's idea of time against
   rekordbox's by a few tens of milliseconds, more than a frame.
3. **A computed ID that does not match the burned disc** (burn gaps, an edit after the burn):
   the player then sees a different disc and shows no cues. Reading the TOC from the drive avoids it.
4. **Hot cues stay out** by decision, so the player's A, B and C are the owner's.
5. **Firmware version and card variants**: one player, one 2 GB card, one firmware (unknown).
