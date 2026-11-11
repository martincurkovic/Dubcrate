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

Python 3.13, [uv](https://docs.astral.sh/uv/), PySide6 (planned), ffmpeg for
decoding and resampling, Nuitka for packaging. macOS first.

| Concern | Choice |
| --- | --- |
| Burning (macOS) | Apple DiscRecording via PyObjC, with `cdrdao` as the alternative backend |
| Burning (later) | `cdrdao` on Linux, TBD on Windows behind the same `Burner` interface |
| Rekordbox reading | `pyrekordbox` (planned) behind a read-only library interface |
| Tags | read-only tag reader (license-dependent, see below) |

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format .
```

Requires `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`). The `cdrdao`
backend also needs `brew install cdrdao`.

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

Not chosen yet.
