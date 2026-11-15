# Writing cues to rekordbox: what is known, and the plan

**Status (2026-11-15): nothing is written to rekordbox.** `AGENTS.md`'s hard rule ("never write to a rekordbox
database") stands. This file records what a read-only look at the owner's real library showed about `djmdCue`,
the risks, and the staged plan. The owner has agreed to stages 1 and 2; stage 3 waits for them to ask for it.

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

1. **Review what changed on the CDJ** (read-only): read the card back, compare it with what Dubcrate wrote (a baseline saved at sync
   time), and list new, moved and deleted cues per track. Needs the baseline, and the player's real files to test the logic on.
2. **Edit memory cues in Dubcrate** (built 2026-11-15, see below): stored in Dubcrate's own database, layered over rekordbox's, and
   used by the player, Sync and the labels. rekordbox is not touched.
3. **Write to rekordbox**: memory cues only, rekordbox closed, a backup of `master.db` and its `-wal` / `-shm` first, a
   transaction, a read-back check, per-track undo, soft deletes, refusal on a schema not seen before; built and tried on a copy first.
   **Not started.**

## Stage 2 as built

- `library/store.py` (schema v13): `memory_cue(path_key, time_ms, label)`, and `memory_cue_set(path_key, edited_at)`. A track
  whose set exists has its **memory cues replaced** by those (made from rekordbox's memory cues the first time it is edited);
  hot cues and loops stay rekordbox's. Keyed by `path_key` (the same file under any row), so it survives a re-import.
- `library/cue_edits.py`: `with_edits(lib, track, cues)` puts the edited memory cues over a track's cues; used for the player and
  for Sync (`card_discs.RekordboxCues.for_track`, `ui/cue_cache.RekordboxCueCache.cues_for`).
- The player: **MEMORY** adds a memory cue at the playhead (on the beat with Q on), **✕** in the Memory list deletes one,
  right-click a row to move it to the playhead or delete it, **Reset** goes back to rekordbox's. An edited track says so.
