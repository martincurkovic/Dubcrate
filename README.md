# Dubcrate

Burn audio CDs for old-style CDJs (CDJ-1000MK3 and friends) from your DJ library.

Pick a folder of tracks, or import a whole rekordbox library. Build disc-sized
playlists that live only in Dubcrate, never in rekordbox. Then burn them to an
audio CD with CD-TEXT through an attached USB burner.

> **Status:** early. The burn pipeline is being proven on real hardware before
> any GUI exists. Nothing here is usable as an app yet.

## Goals

- Import a folder of tracks or a rekordbox library (read-only).
- Playlists capped to the length of an audio CD (74 or 80 min), measured in exact
  sectors, with auto-fill and "split into N discs".
- Render to CD-ready audio (16-bit / 44.1 kHz stereo, dithered) and burn
  disc-at-once, gapless, **with CD-TEXT**.
- Show what rekordbox knows (BPM, key, waveform, cues, beat grid) when a rekordbox
  library is loaded, even though none of it can go on the disc.
- Printable track list for the disc sleeve, since old CDJs have no screen for it.
- Later: MP3 data discs as a secondary mode.

## Non-goals

- No writing to rekordbox's database, ever. Dubcrate keeps its own SQLite store.
- No beat grids, cue points or analysis on the disc. Old CDJs can't read them.
- Not a DJ player and not a rekordbox replacement.

## Stack

Python 3.13, [uv](https://docs.astral.sh/uv/), PySide6, ffmpeg for
decoding and resampling, Nuitka for packaging. macOS first.

| Concern | Choice |
| --- | --- |
| Burning (macOS) | Apple DiscRecording via PyObjC, with `cdrdao` as the alternative backend |
| Burning (later) | `cdrdao` on Linux, TBD on Windows behind the same `Burner` interface |
| Rekordbox reading | `pyrekordbox` (planned) behind a read-only library interface |
| Audio preview | miniaudio (as in AKAISDS), with ffmpeg for formats it can't decode |
| Tags | `ffprobe` (no tag library needed) |

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
```

Requires `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`). The `cdrdao`
backend also needs `brew install cdrdao`.

### Running the app

```sh
uv run dubcrate-gui                        # the app
uv run dubcrate-gui --library ~/try.db     # a separate library, handy for trying things out
uv run python -m dubcrate.ui               # the same, without the installed script
```

Across the top is the preview player: double-click a track to hear it (Space plays
and pauses, the arrows step through the list). It shows the whole track with its
phrases (INTRO, CHORUS, ...) and cues, and a zoomed waveform scrolling past a fixed
playhead with the beat grid (mouse wheel zooms; click either to seek). For rekordbox
tracks these come from rekordbox's own analysis; for anything else the waveform is
computed from the audio. The track list has a **Preview** column with each track's
whole waveform, and a column chooser (right-click the header). **File ▸ Settings**
(Ctrl+,) picks the audio output device and buffer size and has a test tone.

Below that, three panes: your sources (imported folders and rekordbox libraries, with their
playlists) on the left, the tracks in the middle, and the disc you are planning on
the right. Drag tracks onto the disc (or press Return, or **Add to Disc**), drag rows
on the disc to reorder, and watch the bar: it shows each track and goes red past the
end of the disc. **Fit All to Disc** fills a disc in order; **Disc ▸ Split Shown
Tracks Across Discs** cuts a playlist into as many discs as it takes. **File ▸ Import**
reads a folder or your rekordbox library (read-only). The look follows your system
(View ▸ Theme also has Light, Dark and Macintosh System 6, shared with the author's
other apps). Burning is not in the window yet: use `dubcrate burn` below.

### Library

Import a folder of audio files, or a rekordbox library, into Dubcrate's own
SQLite database. Both are read-only: rekordbox's `master.db` is copied to a
temp directory and the copy is read, and audio files are only probed.

```sh
uv run dubcrate import-folder ~/Music/Crate          # directories become browsable nodes
uv run dubcrate import-rekordbox                     # the installed library, or --db PATH
uv run dubcrate sources
uv run dubcrate nodes 1                              # a source's playlist/folder tree
uv run dubcrate tracks --source 1 --search kettama --sort bpm --desc
uv run dubcrate tracks --node 40                     # a playlist, in its running order
```

The library lives in the platform app-data directory; pass `--library PATH` to
use another one. Re-importing a source syncs it in place and keeps track ids.

### Planning and burning discs

Discs are Dubcrate's own playlists, capped to a real CD (74 or 80 min, at most
99 tracks). Lengths are measured with ffprobe when tracks go on a disc, and the
rendered audio is checked against capacity again before anything is burned.

```sh
uv run dubcrate disc new "Warm Up night"             # 80 min, gapless; --minutes 74, --pregap 2
uv run dubcrate disc add 1 --node 40                 # a whole playlist, in order
uv run dubcrate disc add 1 --node 44 --fit           # only what still fits (--strict stops at the first misfit)
uv run dubcrate disc add 1 --track 1475 1473 --at 1  # specific tracks, at a position
uv run dubcrate disc show 1                          # tracks, times, capacity (~ = not yet measured)
uv run dubcrate disc move 1 3 1 && uv run dubcrate disc remove 1 2
uv run dubcrate disc split --node 44 --name "Pop"    # as many discs as it takes, order kept
uv run dubcrate burn 1                               # dry run: checks the disc, burns nothing
uv run dubcrate burn 1 --yes --simulate              # laser-off test write
uv run dubcrate burn 1 --yes                         # really burn (uses up the blank CD-R)
```

If a track disappears from its source on re-import but a disc still uses it, the
track is kept and flagged rather than silently dropped from the disc.

### Burn spike

`spikes/burn_spike.py` renders tracks, builds the disc and burns it. It is a
dry run unless you pass `--yes`.

```sh
uv run spikes/burn_spike.py doctor                 # tools, drive, media
uv run spikes/burn_spike.py tones work/tones       # synthetic test tracks
uv run spikes/burn_spike.py plan work/tones/* --work work/render
uv run spikes/burn_spike.py toc  work/tones/* --work work/render
uv run spikes/burn_spike.py burn work/tones/* --work work/render \
    --backend discrecording --yes --simulate       # laser-off test write, needs a disc in the drive
uv run spikes/burn_spike.py burn ... --backend discrecording --yes
```

Use a blank CD-R (not a CD-RW) and a low speed (10x is this drive's slowest). A
failed burn wastes the disc.

Discs are gapless by default. `--pregap SECONDS` adds silence before tracks 2 and
up; without our override DiscRecording would insert 2 s between every track.

## License

Dubcrate is free software, released under the
[GNU General Public License, version 3 or (at your option) any later version](LICENSE)
(`GPL-3.0-or-later`).

Third-party material: the Macintosh System 6 theme uses the ChiKareGo2 font by Giles
Booth (a recreation of Chicago 12), licensed
[CC BY](src/dubcrate/ui/fonts/README.txt). Dubcrate is not affiliated with Apple,
AlphaTheta or Pioneer DJ; rekordbox is a trademark of its owner.
