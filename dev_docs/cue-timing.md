# Cue timing: rekordbox's time against the audio on the disc

Found 2026-11-23, after a gig where cues set on beat 1 sometimes cut the start of the sound off. Code:
`src/dubcrate/cue_timing.py`.

## What was wrong

rekordbox times an MP3 from the first byte of its first frame. ffmpeg (which `render.render_track` uses, so
what is burned) drops the Info / Xing frame at the start (1152 silent samples) and the encoder delay its tag
records (`delay + 529` samples, `start_pad + 528 + 1` in ffmpeg's demuxer). A time from rekordbox (cue, beat,
loop) is therefore **later than the same place on the disc by up to about 51 ms**, and the card cue, which was
`track start + rekordbox's time`, landed late. Frame rounding (`round(t * 75)`, at most 6.7 ms either way) was
a small part of it.

## How it was measured (read-only)

For each of 700 MP3s in the owner's rekordbox library with a first beat inside 3 s, the offset between
rekordbox's first beat (`.DAT` beat grid) and the first transient in ffmpeg's decode (the attack of the loudest
onset within +-80 ms of the beat), grouped by what the file's first frame says. A click at exactly 1.000 s made
into an MP3 by ffmpeg decodes at 999.9 ms with its Info frame and 1025 ms without (ffmpeg trims correctly).

| first frame | tracks | rekordbox later than ffmpeg by (median) | rule |
|---|---|---|---|
| Info + LAME tag, delay 576 | ~400 | 50.9 ms | `1152 + delay + 529` samples |
| Info + Lavc / Lavf tag, delay 576 | ~145 | 25-27 ms | `delay + 529` |
| no Info / Xing frame | ~120 | 0 ms (within +-4 ms) | none |
| Info, delay 0 (a handful) | 13 | 10 to 38, disagreeing | left alone |
| other encoder names | few | mixed | left alone |

After correcting, the median residual is 0.0 ms in every group, and 82% of all tracks are within 8 ms (the
measurement itself is only good to a few ms; the rest are the rare encoders and some of the 20% of Lavf files
that behave like LAME ones). WAV / FLAC / AIFF / M4A were **not measured** (one WAV in the sample): they are
taken as having no lead. Nothing was burned and played to check it on a CDJ.

## What is converted

Dubcrate's cues are in **audio time** (what the player, the preview and the card use). `to_audio` moves
rekordbox's cues earlier on the way in (`card_discs.RekordboxCues`, which Sync, Import Cues and the finder use,
and `ui/cue_cache.RekordboxCueCache`, for the player), and `rekordbox_write._resolve` moves a cue Dubcrate saves
later on the way out (`_in_rekordbox_time`: a cue rekordbox already has and Dubcrate shows unchanged keeps
rekordbox's own time, so an edit never moves another cue, and a cue within the lead of the start, shown at 0,
stays put). The finder shifts rekordbox's beat grid by the same amount (`track_finder.in_audio_time`) so a cue
on a beat reads as on it.

**The player's analysis too** (`analysis_to_audio`, applied in `ui/preview.load_media`): beat grid, phrases,
the cues in the analysis files and the detail waveform (which loses its first `lead` ms, in whole 1/150 s
columns, so up to 3 ms off) move into audio time as the track loads. So **Q (quantise) snaps to where the
beat is in the audio** and a cue set with it is saved there (and converted back to rekordbox's time on the way
out), and the bar counter, beat jump and grid lines agree with the sound. The whole-track overview waveform
is left as it is (50 ms is a fraction of one of its columns). The deck clamps a snapped cue to 0, and a beat
before 0 (a first beat inside the lead) stays in the grid.

## Rounding and earlier syncs

`card_sync._frame` rounds **down** (a cue is never later than where it was set; up to 13 ms early).
`DiscRequest.earlier` is the disc's baseline (`Library.card_baseline`): `merge_cues` moves a card cue that is
exactly as the last sync left it to the nearest wanted cue within `MOVE_FRAMES` (8), keeping its place, so
re-syncing a disc corrects its cues instead of adding a second one beside each. A cue the player moved is no
longer in the baseline and is left alone; a disc with no baseline gets the corrected cue added beside the old.

## After the review (2026-11-23)

- **Edits an older Dubcrate kept** (memory cues, loop removals waiting in Dubcrate's tables) hold rekordbox's
  unedited cues in rekordbox's own time. When written, `_in_rekordbox_time` also accepts a cue whose time equals
  one rekordbox has (so it is not shifted twice) and a loop removal is tried in both times. Until they are
  written such a cue is shown that much late in the player.
- **A file that can't be read** (an unplugged drive) is skipped by the writer (`lead_or_none`), not written as
  if it had no lead; the edit waits in Dubcrate.
- **A cue we wrote that is 1 or 2 frames later than wanted** is moved, not counted as already there.
- **A very short loop** keeps one frame when its end is floored.
- The Sync list says "N cues to move" (and the track column "N to move") for moved cues.
- Known and left: the whole-track overview waveform is not shifted, and `lead_ms` reads a file's first bytes on
  the GUI thread the first time a track's cues are asked for (the lead is remembered, up to 20,000 files).
