# Card sync: the plan

Goal: a **Sync** tab that writes a Dubcrate disc's cues onto the CDJ-1000MK3's SD card, so the
disc loads in the player with its rekordbox cues already set. The card format is in
[cdj1000mk3-card.md](cdj1000mk3-card.md) (read that first: what is measured, and its
**"Gaps, and where more testing is indicated"**). The module that reads and writes it is
`src/dubcrate/cdj_card.py`. **Status 2026-11-14: Phases 0 to 3 are done: the core, the command line and the Sync tab are built, and a CDJ-1000MK3 showed the
cues of files our writer produced (details in the Handover below). Waveform pre-writing (Phase 4) is not started: it needs the
amplitude scale, for which a test disc is made and waiting (`wave-test-disc.md`).** Built: `cdj_card.py` (the format, `write_card`, `backup_card`), `card_sync.py` (cue
mapping, merge, plan), `card_discs.py` (a Dubcrate disc's TOC and rekordbox cues), `card_report.py`,
`cli_card.py` (`dubcrate card show | backup | toc | sync`, a dry run unless `--yes`), `drive_toc.py` (the TOC of the
audio CD in the drive, macOS), and the Phase 0 tooling
(`spikes/cdj_card_testimage.py`, `cdj_card_restore.sh`, with test images made). **Files written by it have been loaded by a
player** (2026-11-14, firmware 2.20).

## Handover (end of 2026-11-13, with 2026-11-14's additions: read this first)

**State (2026-11-14, end of day).** Built: the card format and its reader/writer (`cdj_card.py`), the cue mapping,
merge and plan (`card_sync.py`), the library glue (`card_discs.py`), reports (`card_report.py`), the command line
(`dubcrate card show | backup | toc | sync`), `card_apply.py` (the shared write), `drive_toc.py`, `eject.py`,
`burn_check.py`, the Sync tab (`ui/sync_view.py`, with its writing **switched on**), reading rekordbox's cues from its
database and drawing them in the preview, and the waveform test disc (`wavetest.py`, `wavefit.py`). **The player has
accepted our files in every case tried** (t1 to t4 below and in `cdj1000mk3-card.md`). Still unproven on hardware: the
Sync tab itself on a real card (the command line is), the post-burn TOC check on a real burn, and the test disc. The owner
has asked for finished items in `TODO.md` to be ticked off (done 2026-11-14).

**Answered by the owner (2026-11-14)**, both from rekordbox:
1. *Which cues are which*: on *The Booth*, *Blessings (Original Mix)* and *I Could Be The One (Original Mix)* the
   owner read hot A, B, C off rekordbox and they match `Kind` 1, 2, 3 to within the owner's "approximately"
   (Booth's B: database 1:25.8, remembered as 1:28). On Blessings, C and D overlap at 4:25: **`Kind` 5 is hot
   cue D**. The distribution agrees (`Kind` 1, 2, 3, 5, 6, 7 = 1,048, 1,010, 947, 428, 2, 1 rows; no 4), so
   `library/rekordbox.py: hot_slot` maps 1, 2, 3 to A, B, C and 5 to 9 to D to H (G and H inferred, never seen);
   a `Kind` 4 would be kept as a memory cue. The two loops are hot D saved as loops. Hot D is often 2 ms from
   C on the owner's tracks, so the 2-frame merge folds the pair into one memory cue.
2. *Skip memory cues in the first second?* **No: keep them all.** They are the track's start, useful when
   mixing a track in from the beginning on a CDJ. Nothing to build.

**The hardware session (the owner, Phase 0 below)**, in order: note the player's **firmware version**; the write
test with the two images already in `~/cdj-card/` (`t1-cues-swapped-cd1`, `t2-cd2-file-from-scratch`, each
made from a copy; `cdj_card_restore.sh` writes one to the card, the owner runs it: it needs `sudo`); with a small
Dubcrate burn in the drive, `dubcrate card toc DISC` (it says whether the computed TOC is the real one, which
checks `toc_from_lengths`, and shows the drive's actual output if `drutil` words anything differently); copy the audio of CD 1 tracks 1 to 3 and CD 2 and 3 track 1 for the waveform fit.
**Write test, 2026-11-14: (a), (b) and (c) passed, and so did the whole path run by our own command** (firmware 2.20). `t1`: the player loaded CD 1's cues from a file we wrote, byte for byte untouched afterwards. `t2`: a cues-only file for a disc that had an index entry and no file was accepted, and the player then grew it to 1,550 bytes (12 `W001` and 12 `W002` records: slots up to the highest track played) keeping our cues exactly; our parser round-trips that file byte for byte. `t3` (a disc new to the card): the index and the other discs' files untouched, `CDJ00004.PDJ` grown by the waveform records of the two tracks played. `t3`: a disc the card had never seen, given an index entry and a file by our writer: its cues and loop showed. **`t4`: `dubcrate card sync 4 --from-drive --yes` itself, on a card the player had set up, with a real CD's TOC read from the drive: all 81 cues showed** (see the table in `cdj1000mk3-card.md`). **Still open: (d) a disc Dubcrate burned** (which checks `toc_from_lengths`, the TOC the Sync tab computes: it has no `--from-drive`), **and the first card's unexplained failure** (a card that stopped being detected after the first write, before the flush was added and perhaps pulled without ejecting; `cdj1000mk3-card.md`, "The first card stopped being detected"). Eject before pulling, always. `WRITING_ENABLED` was switched **on** later the same day (2026-11-14), with **Eject after syncing** (`eject.py`) and the post-burn TOC check (`burn_check.py`, wired into the burn window: **not yet run on a real burn**, and it changes the burn flow slightly: an audio burn on a Mac now leaves the disc in the drive until the check has run, then ejects it; a failure of the check is only logged and the disc is ejected whatever happened). The owner's reading of the first card's failure: a flaky cheap generic card. After it: image the card
(`cdj_card_snap.sh 11-after-t1 /dev/diskN`) and `cdj_card_dump.py dump/diff` to see what the player did to
our file.

**Done 2026-11-14, without hardware** (none of it tried on a drive): (1) the disc's TOC from the drive:
`drive_toc.read_drive_toc` (`drutil toc`, macOS only, the runner injectable; it was only ever seen printing an
audio CD's TOC, so what it prints for no disc, a blank or a data disc is a guess: those are refused with its own
first line quoted), `dubcrate card toc [DISC]` (prints it, and says whether the disc as planned has the same
TOC), `dubcrate card sync DISC --from-drive` (one disc: the ID and cue positions come from the real disc, no
measured lengths needed, and it warns if the disc differs from the plan), and `card_discs.toc_differences`;
(4) rekordbox's **database** cues are now drawn in the preview's overview and zoomed waveform and in the track
list's Preview column (`ui/cue_cache.py`: the database is read once on a worker and again when `master.db` or
its `-wal` changes in size or date; a failure is one status message and the track plays on). (5) **the Sync tab** (`ui/sync_view.py`, a fixed
bottom tab laid out after rbxport's Sync Manager, the owner's reference): the card panel (a card is found by
its `PIONEER/CDJ` folder when the tab opens or on Refresh, or a folder is chosen by hand), the audio discs in
their folders with a status each (`card_discs.survey_discs`) and the dry run for the one picked, tick boxes,
Measure Lengths, and a Sync button (**`sync_view.WRITING_ENABLED = True` since the write test passed on the player**, later the same day). Behind it the whole write is built: the question (`confirmation_text`),
then `card_apply.apply_sync` on a worker (the card read and planned fresh, its files copied to `card-backups`
first, `write_card` with its read-back; `card sync --yes` uses it too). The guide's section and picture are done (the picture is staged: invented cues, a pretend card). Eject after syncing was built later the same day. **Still to do**, best first:
`Measure Lengths`, the list and the tab's write tried against a real card; (3) the waveform scale (**tried 2026-11-14, not found by the firmware route: see `cdj1000mk3-card.md`, "The waveform"; the plan now is a disc of known test tones, made and waiting to be burned and played: `wave-test-disc.md`**): Ghidra and `openjdk@25` are installed by Homebrew (`analyzeHeadless` is not on `PATH`: look in
the Caskroom) and the firmware is still `~/Downloads/CDJ1000MK3_061023.EXE`, the `/tmp` extraction is gone
(`dev_docs/cdj1000mk3-card.md`, "Reproducing the analysis"); or fit it from the card's five waveform records
against the audio, which needs the owner to copy the tracks.

**Guesses in the code, each to be checked and none a measurement**: `DUPLICATE_FRAMES = 2`;
`toc_from_lengths` for a disc with gaps (and `pregap_sectors`' unit: `burning.md`); `IndexFile.next_name`
(highest + 1; numbering after a deleted disc is unknown); `write_card(create=True)` (a card with no
`PIONEER/CDJ`: the player has not been shown one made this way); the flags word `0x0020` in a disc's file; the
`HC11` length field (0 or 60); the loop's last field `0x00010000`; the hot cue slot numbers (C = 3 never seen).

**Gotchas that cost time**
- `sudo` needs a real terminal: the agent cannot run `cdj_card_snap.sh` or `cdj_card_restore.sh`, and must never
  write to the owner's card or mount it (images in `~/cdj-card/` are theirs, outside the repo; so are the
  `CD1 TOC.txt` etc., with copies in `tests/data/cdj_card/`).
- `hdiutil attach` gives the image a **different disk number each time**: take it from the command's output.
  Mount images `readOnly`; `cdj_card_testimage.py` is the only thing that mounts one read-write, and only a copy.
- `ruff format` re-wraps lines, so a scripted exact-string edit written from memory fails (or, worse, a
  slice-and-reindent edit mangles a file: it happened to `cli_card.py`): read the region first.
- rekordbox: to look at its data use `dubcrate --library <scratch> import-rekordbox` (a snapshot copy) and a
  script with `pyrekordbox.masterdb.MasterDatabase` on `rekordbox._snapshot`: never the live `master.db`.
- The card's files carry the date 1 Jan 1970 and the player ignores the Mac's `.Spotlight-V100`. Files macOS
  writes carry today's date: a possible (unproven) reason if the player ignores a test image.
- The owner would rather not waste discs (a memory note): prefer data already captured; one rewritable disc
  of test tones if a disc is unavoidable; ask before any burn.

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
rekordbox cues (master.db) ─┐
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
- **Where rekordbox's cues are**: its database (`djmdCue`), not the analysis files (those have none for a
  local library: 4,310 cues over 1,061 of 1,065 tracks in the owner's). Read from a snapshot copy at sync
  time (`library/rekordbox.py: read_cues`), never stored in Dubcrate's library. `Kind` 0 is a memory cue and
  1, 2, 3, 5, 6, 7 hot cues A to F (`hot_slot`: **read off the owner's library, confirmed by them on three
  tracks**; `Kind` 5 is hot D, usually 2 ms from hot C, so the 2-frame merge folds the pair into one cue).
  The `Kind` 0 cue within the first 0.2 s that most tracks have is **kept**: the owner wants it (a track's
  start, for mixing in from the beginning).
- **Only tracks that are in rekordbox give any**: a track from the Explorer or a plain folder has none
  unless it is the same file as a rekordbox track (`Library.rekordbox_twin`).

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
| rekordbox cues to card entries, merge, plan | `src/dubcrate/card_sync.py` (**done**, pure, no Qt; 24 tests on the real card files) | nothing (the `Kind` meanings were confirmed by the owner, 2026-11-14) |
| Backup, write, verify, eject | same, with a thin I/O layer | the write test |
| Command line | `dubcrate card show / backup / sync DISC... --card PATH [--dry-run] [--yes]` | the above |
| Sync tab | `ui/`: a tab **pinned at the bottom** of `source_tabs.py`'s strip (it has Library, Explorer and pins now, all added "at the bottom": a fixed bottom group needs a small change), a middle pane with the discs and the card | the above |
| TOC from the drive | `src/dubcrate/drive_toc.py` (**done**: `drutil toc`, macOS; `card toc [DISC]`, `card sync DISC --from-drive`) | a disc in the drive to try it on |

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
3. **Confirm what rekordbox's cue kinds mean** (no need to set any: the library already has 4,310). Open
   rekordbox and look at three tracks, which `dubcrate card sync` lists (these three are in the owner's
   library): *The Booth* (Hills), *Blessings (Original Mix)* (Calvin Harris, Clementine Douglas) and *I
   Could Be The One (Original Mix)* (Avicii vs. Nicky Romero). The database has, in seconds: Booth `Kind 0`
   0.051, `1` 60.051, `2` 85.832, `3` 196.926; Blessings `0` 0.075, `1` 59.149, `2` 147.761, `3` 265.910,
   `5` 265.912; I Could Be The One `1` 90.153, `2` 134.684, `3` 390.152. Say which are hot cues A, B, C,
   whether the 0.05 s ones are memory cues you made or the automatic cue point, and what the 265.912
   one is.
4. **A burned disc's TOC**: burn a small Dubcrate disc, `drutil toc` it, save the output, so
   `toc_from_lengths` is checked.
5. Optional, as no longer blocking: set hot cue A on a second track while A exists on a first,
   to confirm there are only three hot cue slots per disc.
6. For the waveform, **copy the audio** of the played tracks off the CDs (macOS mounts an audio CD
   as AIFF files): CD 1 tracks 1 to 3, and track 1 of CD 2 and of CD 3. Kept locally, never added.

### Phase 1: the pure core and the command line: **built, and proven on a player**

`card_sync.py` (cue mapping, merge, plan), `card_discs.py` (the library half), `card_report.py`, and
`dubcrate card show | backup | sync`, with tests on the real card files (`tests/test_card_sync.py`,
`test_card_discs.py`, `test_cli_card.py`). Phase 0 step 2 said the player takes our file (2026-11-14), and the
rekordbox cue kinds were checked on real cues (step 3).

### Phase 2: writing: **built**

`card sync --yes` (and the tab, through `card_apply.apply_sync`) backs up, writes (files, then the index), flushes and reads back;
`--from-drive` reads the TOC from the disc in the drive; the tab can eject. Left: a card with no `PIONEER/CDJ` yet
(`write_card(create=True)` exists and is **untried on a player**: the player has to set a card up first, as it did for the
second card), and the TOC of a disc **Dubcrate burned** compared with the computed one (the burn window now checks it).

### Phase 3: the Sync tab: **built**

The fixed bottom tab, the status column, dry run and confirmation, the user guide page and its
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
2. **The cue kinds are read off one library, confirmed by the owner on three tracks, not documented**, and so is whether a cue's time lines
   up with the rendered audio: an MP3's encoder delay can shift the player's idea of time against
   rekordbox's by a few tens of milliseconds, more than a frame.
3. **A computed ID that does not match the burned disc** (burn gaps, an edit after the burn):
   the player then sees a different disc and shows no cues. Reading the TOC from the drive avoids it.
4. **Hot cues stay out** by decision, so the player's A, B and C are the owner's.
5. **Firmware version and card variants**: one player, one 2 GB card, one firmware (unknown).
