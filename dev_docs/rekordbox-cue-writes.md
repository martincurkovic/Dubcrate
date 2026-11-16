# Writing cues to rekordbox: what is known, and the plan

**Status (end of 2026-11-15): stages 1 (additions only), 2 and 3 are built.** Memory cues can be edited in the player, saved into
rekordbox's `master.db` (the command line was run by the owner on their live library and worked; the in-app path is not tried live)
and imported back from the card. `AGENTS.md`'s hard rule has ONE exception, `rekordbox_write.py`, for memory cues only. This file
records what a read-only look at the owner's real library showed about `djmdCue`, the risks, the plan and how each stage was built.

## What `djmdCue` holds (measured on the owner's library, a snapshot copy, 2026-11-15)

4,318 rows (4,312 live, 6 soft-deleted), over 1,061 of 1,065 tracks. 29 columns. What each did in this library:

| Column | What was seen |
| --- | --- |
| `ID`, `UUID`, `ContentID`, `ContentUUID` | `ID` a 10-digit integer, `UUID` a v4 UUID; both the cue's and its track's |
| `Kind` | 0 memory cue (882), 1, 2, 3 hot A, B, C (1,048 / 1,010 / 947), 5 hot D (428), 6 and 7 (3 rows); none are 4 |
| `InMsec` | the position in milliseconds (an integer) |
| `InFrame` | **`floor(InMsec × 0.15)` on 4,197 of 4,312 rows (97.3%)**: 150 frames a second. The other 115 rows have **0**, so rekordbox lives with a 0 here |
| `InMpegFrame`, `InMpegAbs` | **0 on 4,250 of 4,312 rows** (all MP3s here); 57 rows have values (`InMpegFrame` ≈ `InMsec × 0.075`). A new row can leave them 0 |
| `OutMsec`, `OutFrame`, `OutMpeg*` | a loop's end (`OutFrame` is `floor(OutMsec × 0.15)` too); -1 or empty when there is none |
| `InPointSeekInfo`, `OutPointSeekInfo` | **empty on every row**: no binary field to write |
| `Color`, `ColorTableIndex` | memory cues: `Color` -1 (519 rows) or 255 (363), `ColorTableIndex` empty; hot cues mostly -1 / empty |
| `ActiveLoop`, `BeatLoopSize`, `CueMicrosec` | empty or 0 almost everywhere; the two loops have `BeatLoopSize` 8388609 and `ActiveLoop` 0 |
| `Comment` | optional; 259 memory cues have one |
| `usn`, `rb_local_usn`, `rb_data_status`, `rb_local_data_status`, `rb_local_synced` | **all empty or 0 on every row: this library does not use rekordbox's cloud sync** (the owner has none), so there is no change-tracking to keep in step |
| `rb_local_deleted` | 6 rows are 1: **rekordbox soft-deletes** (a hot cue's row stays, flagged). Dubcrate's reader already skips them |
| `created_at`, `updated_at` | timestamps with milliseconds |

Per track: 803 tracks have memory cues, **at most 3** each (726 have one, 75 two, 2 three). Memory cues near 0 s are the owner's
habit of marking a track's start.

## What this means for the risk

- A **new memory cue** is one row that can copy a sibling's shape (`Kind` 0, `Color` -1, the rest empty) with a new `ID` and
  `UUID`, `InMsec`, and `InFrame = floor(InMsec × 0.15)`. No binary field, no cloud bookkeeping, no MPEG frame to work out.
- **Moving** one changes `InMsec` and `InFrame` together. **Deleting** would follow rekordbox's own practice (`rb_local_deleted = 1`).
- `pyrekordbox` 0.5.0's `Rekordbox6Database.commit(autoinc=True)` refuses to run while rekordbox is open and bumps its USN counters
  (which here are all empty anyway).
- Still **unknown**: how rekordbox 7 reacts to a row it did not write (it is expected to be fine: 115 rows have `InFrame` 0, which
  says it is tolerant); whether it keeps anything else about cues in memory or in the `share` folder's files; and any
  rekordbox update's schema changes. Hot cues E to H and memory loops are the least checked kinds.

## The plan

1. **Review what changed on the CDJ** (read-only): **built 2026-11-15 for additions only** (`card_import.py`, Sync tab > Import Cues…; see
   `card-sync.md`, "Importing cues from the card"). Moves and deletions on the player need a baseline of what a sync wrote (the card's cues
   after each sync, per disc ID): **not built**.
2. **Edit memory cues in Dubcrate** (built 2026-11-15, see below): stored in Dubcrate's own database, layered over rekordbox's, and
   used by the player, Sync and the labels. rekordbox is not touched.
3. **Write to rekordbox**: memory cues only, rekordbox closed, a backup of `master.db` and its `-wal` / `-shm` first, a
   transaction, a read-back check, soft deletes, refusal on a schema not seen before; built and tried on synthetic databases first.
   **Built 2026-11-15, see "Stage 3 as built"; the owner ran it on their real library (rekordbox 7.2.19.0342) the same day and reported that it worked perfectly.**

## Stage 2 as built

- `library/store.py` (schema v13): `memory_cue(path_key, time_ms, label)`, and `memory_cue_set(path_key, edited_at)`. A track
  whose set exists has its **memory cues replaced** by those (made from rekordbox's memory cues the first time it is edited);
  hot cues and loops stay rekordbox's. Keyed by `path_key` (the same file under any row), so it survives a re-import.
- `library/cue_edits.py`: `with_edits(lib, track, cues)` puts the edited memory cues over a track's cues; used for the player and
  for Sync (`card_discs.RekordboxCues.for_track`, `ui/cue_cache.RekordboxCueCache.cues_for`).
- The player: **MEMORY** adds a memory cue at the playhead (on the beat with Q on), **✕** in the Memory list deletes one,
  right-click a row to move it to the playhead or delete it, **Reset** goes back to rekordbox's. An edited track says so.

## Stage 3 as built (2026-11-15)

`src/dubcrate/rekordbox_write.py` + `cli_rekordbox.py`: **`dubcrate rekordbox write-memory-cues [--db PATH] [--backup-dir DIR] [--keep-edits] [--yes]`**.
It is the only code that writes a rekordbox database (the owner allowed exactly this).

- **Dry run unless `--yes`.** It plans from a snapshot copy and prints, per track, the memory cues rekordbox has now and after.
- **Refuses** while rekordbox is running, and when `djmdCue`, `djmdContent` or `agentRegistry` have columns other than pyrekordbox 0.5.0 knows
  (a rekordbox update). The real file is SQLCipher-encrypted, so every check goes through pyrekordbox's session, never plain `sqlite3`.
- **Matches on `path_key`** to ONE live `djmdContent` row; a file that rekordbox lists twice, or not at all, is skipped and keeps its edit.
- **Copies `master.db`, `-wal`, `-shm` and `masterPlaylists6.xml`** to `rekordbox-backups/master-YYYYmmdd-HHMMSS/` before opening it for writing.
- **One transaction through `commit(autoinc=True)`**, as rbxport does (the owner's recent rows have consecutive `rb_local_usn`, which is what
  `autoincrement_local_update_count` makes): `agentRegistry.localUpdateCount` goes up by the changes pyrekordbox saw (more than one per row) and each
  changed row gets an `rb_local_usn` from it.
- **Per track:** a cue at the same time is kept (a label change edits `Comment`); the rest are paired in time order so a moved cue changes
  `InMsec` / `InFrame` in place; extras are **soft deleted** (`rb_local_deleted = 1`, as rekordbox does); new ones copy the newest plain memory
  cue's `Color` / `ColorTableIndex` / `ActiveLoop` (else 255 / 0 / 0), with `Kind` 0, `Out*` -1 / 0, `InFrame = floor(InMsec × 0.15)`, a new
  28-bit random `ID` (pyrekordbox's `generate_unused_id`), a new `UUID` and the track's `UUID` as `ContentUUID`. The track's **`CueUpdated`**
  counter (a string) goes up by one and its `updated_at` is set. Times are UTC to the millisecond (`... +00:00`, which pyrekordbox's
  `DateTime` type writes).
- **Hot cues, memory loops, other tracks' cues and every other table are not touched**; a memory loop (a `Kind` 0 row with an out point) is not a
  plain memory cue and is left as it is.
- **Reads it back** from a fresh snapshot: `PRAGMA integrity_check` is `ok`, the track and cue row counts are right, each written track's memory
  cues are what was asked, and no other track's cues changed. **Anything wrong puts the backup's files back** (the sidecars it did not have are
  removed) and says so.
- **Afterwards** Dubcrate drops its edits for the written tracks, and for those already equal to rekordbox's (`--keep-edits` keeps them), so
  later changes made in rekordbox show through again.

### In the app (built 2026-11-15, after the command line worked)

The owner chose: **writes on by default at edit time, but the library opens read-only until the user agrees.** The first time a memory
cue on a rekordbox track is added, moved or deleted, `ask_enable_editing` asks; the answer (`rekordbox/editing`, True or False, kept for
good) is also the **Settings > Library** checkbox. Declined = the old behaviour: edits stay in Dubcrate's table.

- `ui/rekordbox_editor.py: RekordboxEditor` (made by `PreviewController`): the edit is saved into Dubcrate's own `memory_cue` rows at once (the
  player redraws instantly and nothing can be lost), then `apply_write` runs on a worker for that track. When it succeeds the cue cache
  re-reads the database (`RekordboxCueCache.refresh`, checked with `is_current` so a read begun before the write is not trusted) and only then are
  Dubcrate's rows for the track dropped (if the track was not edited again meanwhile): no flicker, and the track shows rekordbox's own cues.
- **rekordbox open:** `apply_write` refuses, the edit stays in Dubcrate (the status bar says "Kept in Dubcrate only ... close it first") and the
  next edit of that track writes the whole list. Edits on other tracks that were left waiting are not flushed by this: **File > Save Waiting Memory Cue Edits to rekordbox** does (`RekordboxEditor.write_waiting`: queues every `memory_cue_sets()` key against the first imported rekordbox source, asks the first-edit question again if the answer was no, runs through the same `_start` worker, and says how it ended; keys not in that database come back as `skipped` and stay in Dubcrate; **with two rekordbox sources only the first is tried**; built and tested with the stand-in writer, not tried live), and so does the command line.
- **Backups:** `apply_write(..., keep_backup=False)` deletes its copy after a good read-back. The first write of a session keeps its copy (the state
  before anything this session), the later ones don't; `prune_backups` keeps the newest 20 `master-*` folders.
- A track that isn't in rekordbox (an Explorer or folder file) is never asked about and never written; its edits are Dubcrate's, as before.
- Tests: `tests/test_ui_rekordbox_editor.py` (a stand-in cache and writer: the question asked once and remembered, a decline writes nothing, write
  then drop, backup once, a failure keeps the edit, an edit during a write is written too, the checkbox). **The whole path with a real rekordbox
  is not tried.** Live check: with rekordbox closed, edit a cue in the player, accept the question, open rekordbox: the cue is there.

### What the tests cover, and what they cannot

`tests/test_rekordbox_write.py` builds a synthetic `master.db` with pyrekordbox's own models (NULLs allowed as in the real file): adding, moving,
removing, label-only changes, soft delete, row shape, bookkeeping columns, other rows untouched, no-op, refusal (running, unknown columns), a
failed commit and a bad read-back both restoring, and a guard that `commit(autoinc` appears nowhere else. The dry run was also tried against a
snapshot copy of the owner's real library (schema check passed, a real track's cues matched).
**Not covered, and only a real rekordbox can say:** whether rekordbox 7.2.19 reads a cue row it did not write without complaint, whether it
notices the change on opening (it should: the file changed), whether the USB export carries the cue, and whether `CueUpdated` / the counter
jump are what it expects.

### Live test (the owner ran it on 2026-11-15 and said it "worked perfectly"; which checklist steps they checked was not recorded)

1. rekordbox **closed**. Your backup of the library is the safety net; Dubcrate makes another (`rekordbox-backups`).
2. In Dubcrate edit the memory cues of **one** track (add one, move one, delete one). `dubcrate rekordbox write-memory-cues` (dry run): read what it says.
3. `dubcrate rekordbox write-memory-cues --yes`. Open rekordbox: the track should have exactly those memory cues and no error.
4. In rekordbox move and delete one of them, close and reopen: still as you left it. Export the track to a USB: the cue is on the stick.
5. If anything is wrong: close rekordbox and copy the files in the newest `rekordbox-backups/master-...` over `~/Library/Pioneer/rekordbox/`.

## Memory loops (2026-11-16)

**In Dubcrate (built, tried offscreen only):** MEMORY with a loop on saves a memory loop into Dubcrate's own `memory_loop` table
(schema v14), **apart from `memory_cue` on purpose**: the writer reads `Library.memory_cue_sets()`, so a loop can never reach it as a
plain cue at its start. `with_edits` adds saved loops (`Cue.local`) to a track's cues, they reach the card as memory loops, and they
count in the ten places. Nothing about them is written to rekordbox.

### What rekordbox 7.2.19 wrote (read from a snapshot, 2026-11-16)

Until that day the owner's library had no memory loop at all (4,313 live cue rows, two loops, both hot D saved as loops: `Kind` 5, `BeatLoopSize`
8388609). The owner then made three memory loops and a memory cue in rekordbox on **DANGER DANGER (Extended)** (130 bpm), and
`spikes/rekordbox_loop_rows.py` (read-only, snapshot copy) showed:

| | 4-beat loop | 8-beat loop | loop set by hand | plain memory cue |
|---|---|---|---|---|
| `Kind` | 0 | 0 | 0 | 0 |
| `InMsec` / `OutMsec` | 3809 / 5655 | 7501 / 11193 | 13039 / 13501 | 19039 / NULL |
| `BeatLoopSize` | 262145 (`4 << 16 \| 1`) | 524289 (`8 << 16 \| 1`) | **0** | NULL |
| everything else | `InFrame` 0, `OutFrame` NULL, `InMpegFrame` 0, `InMpegAbs` 0, `OutMpegFrame` NULL, `OutMpegAbs` NULL, `Color` 255, `ColorTableIndex` 0, `ActiveLoop` 0, `Comment` '', `CueMicrosec` NULL, `*SeekInfo` NULL, `rb_local_usn` set (92318, 92320, 92322, 92323: loops take two each), `usn` NULL | same | same | same |

- **A memory loop is a `Kind` 0 row with an out point after its in point**: exactly what `rekordbox.cue_from_row` already reads as `loop` with no slot.
- **`BeatLoopSize` = `beats << 16 | 1`** for a loop made with a beat size (the hot D loops' 8388609 is `128 << 16 | 1`, 128 beats); **0 for a loop set by hand**.
  What fractions of a beat give is not known, so a loop saved in Dubcrate (it has no beat size) is written with **0**.
- **rekordbox 7.2.19 no longer fills the frame columns** (`InFrame` 0, `OutFrame` NULL, in all 101 of the rows it made in 2026-10 too); older rows (and
  Dubcrate's memory cue writer) have `floor(InMsec x 0.15)`. Both are read fine, so a loop is written as rekordbox now writes one.

### The writer for loops (built 2026-11-16, tested on synthetic databases only)

`rekordbox_write.py`: `plan_write(..., loops=)` / `apply_write(..., loops=)` take `{path_key: [(start ms, end ms, label)]}`. **Loops are only ever
added**: a loop rekordbox has within `SAME_LOOP_MS` (50 ms) at both ends is not added again, a soft-deleted loop and a hot cue's loop (`Kind` != 0) do
not count as a memory loop, a loop that ends before it starts is skipped, and no row rekordbox has is changed or removed for a loop. A new row copies
`Color` / `ColorTableIndex` / `ActiveLoop` from the newest memory cue and the rest from `LOOP_SHAPE` (the table above). It is the same one transaction,
backup, `CueUpdated` bump (once a track), read-back (`PRAGMA integrity_check`, the row counts, each track's loops as expected, no other track changed)
and restore as for the memory cues. `tests/test_rekordbox_write.py` has the shape, the add-only rules, the read-back restore and the CLI.

**Switched on in two steps (the owner asked for loops to be tested before they are written): the command line first, then the app.**
- The command line: `dubcrate rekordbox write-memory-cues --loops` (a dry run unless `--yes`, and nothing about loops without `--loops`).
- The app: `RekordboxEditor.WRITE_LOOPS`, **True since 2026-11-16**, after the live test below worked. MEMORY with a loop on asks the first-edit question
  (which then says "memory cues and loops"), saves the loop in Dubcrate and queues it like a memory cue edit (`editor.saved`); the Dubcrate copy is
  dropped once rekordbox has been re-read and has the loop. With it False a saved loop stays in Dubcrate only and the status bar says so. This path is
  tested with a stand-in writer; **not tried live in the app**.

**Known limit (decided with the owner, 2026-11-16: loops are added beside rekordbox's):** once a loop has landed in rekordbox it is rekordbox's, and
Dubcrate cannot delete it (a rekordbox loop has no ✕). Deleting a loop saved in Dubcrate that has not been written yet is fine. If this is wanted,
the memory cues' way (Dubcrate keeps the whole list, an edited list replaces rekordbox's) would need a `memory_loop_set` like `memory_cue_set`.

**The live test (the owner runs it, on a copy first):**
1. Close rekordbox (the command checks for it, even for a copy). Copy `~/Library/Pioneer/rekordbox/master.db` (and its `-wal` / `-shm` if present) to a
   scratch folder. In Dubcrate save a loop on a track that is in rekordbox (MEMORY with a loop on; with `WRITE_LOOPS` off it stays in Dubcrate).
2. `dubcrate rekordbox write-memory-cues --loops --db SCRATCH/master.db` (a dry run): read what it adds. Then again with `--yes`, and look at the result
   with `uv run python spikes/rekordbox_loop_rows.py --db SCRATCH/master.db --title TRACK`: the new row should look like the table above.
3. On the real library, after your own backup (Dubcrate makes one too): the same command without `--db`, then open rekordbox: the loop is in the track's
   memory list, it loops, survives a restart and goes onto a USB export.
4. **Done 2026-11-16: the owner ran steps 1 to 3 (a loop on "SOS" by "1111"): it was in rekordbox and behaved as expected. `WRITE_LOOPS` is on.**
