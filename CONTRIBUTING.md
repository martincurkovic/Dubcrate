# Developing Dubcrate

Notes for working on Dubcrate itself. For what the app does and how to use it, see
the [README](README.md). AI coding agents should also read [AGENTS.md](AGENTS.md).

## Set up

You need a Mac (the primary platform), [uv](https://docs.astral.sh/uv/), and
`ffmpeg`/`ffprobe` on your `PATH` (`brew install ffmpeg`). uv fetches Python 3.13.
The optional `cdrdao` burn backend needs `brew install cdrdao`.

```sh
uv sync                                    # install everything
uv run dubcrate-gui                        # run the app
uv run dubcrate-gui --library ~/try.db     # ...with a separate library, to try things safely
uv run pytest                              # tests (no sound card, drive or disc needed)
uv run ruff check . && uv run ruff format .
```

`uv run python -m dubcrate.ui` starts the app without the installed script.

## How it fits together

| Concern | Choice |
| --- | --- |
| GUI | PySide6 (Qt 6), themed like the author's other apps (System / Light / Dark / Macintosh System 6) |
| Packaging | Nuitka (not set up yet) |
| Burning (macOS) | Apple DiscRecording through PyObjC; `cdrdao` as the alternative backend |
| Burning (later) | `cdrdao` on Linux; Windows to be decided; all behind one `Burner` interface |
| Rekordbox | `pyrekordbox`, reading a temporary copy of `master.db` and the analysis files |
| Audio preview | miniaudio, with ffmpeg for formats it can't decode (M4A, AIFF) |
| Tags and lengths | `ffprobe`; no tag library |
| Storage | Dubcrate's own SQLite library, separate from rekordbox |

```
src/dubcrate/
  cd.py render.py planning.py build.py   Red Book maths, audio rendering, disc packing, disc -> burnable
  burn/                                  burn backends (discrecording, cdrdao)
  library/                               SQLite store, folder and rekordbox importers, length measuring
  audio/                                 devices, decoding, the preview player
  waveform.py analysis.py                3-band waveforms; rekordbox analysis files
  cli.py cli_discs.py                    the `dubcrate` command-line tool
  ui/                                    the PySide6 window
tests/                                   pytest, run offscreen
spikes/                                  throwaway hardware drivers
dev_docs/                                command-line reference, burning and hardware notes, roadmap
```

[AGENTS.md](AGENTS.md) has the module-by-module notes and the rules the code relies on.

## Things to know before changing code

- **Burning uses up real discs.** Everything that can burn defaults to a dry run and
  needs `--yes`. Read [dev_docs/burning.md](dev_docs/burning.md) before touching it.
- **Rekordbox is only ever read.** Never write to its database or analysis files.
- **The SQLite library belongs to the GUI thread.** Worker threads compute and
  return results; the GUI thread stores them.
- **Tests never open a sound card.** `tests/conftest.py` swaps in a fake device.
- Look at the window in all three looks (dark, light, system6) after changing it.

## Documentation

| | |
| --- | --- |
| [dev_docs/command-line.md](dev_docs/command-line.md) | the `dubcrate` command-line tool |
| [dev_docs/burning.md](dev_docs/burning.md) | the burn spike, hardware findings, what is verified |
| [dev_docs/rekordbox-analysis.md](dev_docs/rekordbox-analysis.md) | what we read from rekordbox, and what was measured |
| [dev_docs/audio.md](dev_docs/audio.md) | miniaudio and the preview player |
| [dev_docs/roadmap.md](dev_docs/roadmap.md) | what is next, decisions made, rough edges, and what to do before a public release |

## License

Dubcrate is [GPL-3.0-or-later](LICENSE). By contributing you agree your work is
released under the same terms.
