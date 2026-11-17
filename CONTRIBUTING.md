# Developing Dubcrate

Notes for working on Dubcrate itself. For what the app does and how to use it, see
the [README](README.md). AI coding agents should also read [AGENTS.md](AGENTS.md).

## Set up

You need [uv](https://docs.astral.sh/uv/) and `ffmpeg`/`ffprobe` on your `PATH` (a built app bundles its own, see `BUILDING.md`).
uv fetches Python 3.13. A **Mac** is the primary platform (`brew install ffmpeg`; the optional `cdrdao` burn backend is `brew install cdrdao`).

**Windows** (early; nothing here has been run on a real Windows machine yet): install ffmpeg (for example `winget install Gyan.FFmpeg`), then `uv sync` and `uv run dubcrate-gui` or `uv run pytest` in PowerShell. Dubcrate can't burn on Windows yet (the burn window says so, and **Disc > Export Disc as Files…** makes a disc for another program). `BUILDING.md` has the installer build; the tests also run on Windows by hand (`test-windows.yml`).

**Linux** (early; burning from the window is not proven there): install `ffmpeg` and `cdrdao` from your package manager (MP3 discs' images are made by Dubcrate itself; `xorriso` is only an optional cross-check in one test), and Qt's libraries for the X11 plugin. The exact packages and a step-by-step
first-session checklist are in [dev_docs/linux-test-plan.md](dev_docs/linux-test-plan.md). With Docker running,
`scripts/test_linux.sh` runs the whole suite on Ubuntu from any OS.

```sh
uv sync                                    # install everything
uv run dubcrate-gui                        # run the app
uv run dubcrate-gui --library ~/try.db     # ...with a separate library, to try things safely
uv run pytest                              # tests (no sound card, drive, disc or network needed)
uv run ruff check . && uv run ruff format .
```

`uv run python -m dubcrate.ui` starts the app without the installed script.

## How it fits together

| Concern | Choice |
| --- | --- |
| GUI | PySide6 (Qt 6), themed like the author's other apps (System / Light / Dark / Macintosh System 6) |
| Packaging | Nuitka (via `pyside6-deploy`) and dmgbuild: [BUILDING.md](BUILDING.md) |
| Burning (macOS) | Apple DiscRecording through PyObjC; `cdrdao` as the alternative backend |
| Burning (Linux) | `cdrdao`, not yet tried on a real drive |
| Burning (Windows) | none yet (Export Disc as Files); all behind one `Burner` interface |
| Rekordbox | `pyrekordbox`, reading a temporary copy of `master.db` and the analysis files |
| Audio preview | miniaudio, with ffmpeg for formats it can't decode (M4A, AIFF) |
| Tags and lengths | `ffprobe`; no tag library |
| Storage | Dubcrate's own SQLite library, separate from rekordbox |

```
src/dubcrate/
  cd.py render.py planning.py build.py   Red Book maths, audio rendering, disc packing, disc -> burnable
  mp3.py iso.py export.py loudness.py    MP3 discs and their ISO image, export as files, level matching
  labels.py units.py                     index labels for the case: layout and pagination; sizes in mm / cm / in (no Qt)
  burn/                                  burn backends (discrecording, cdrdao) behind one interface
  library/                               SQLite store, folder and rekordbox importers, length / waveform / level measuring
  audio/                                 devices, decoding, the preview player
  waveform.py analysis.py wave_model.py  waveforms (rekordbox's and the CDJ's), rekordbox analysis files
  cdj_card.py card_*.py                  the CDJ-1000MK3's SD card: format, sync planning, import, formatting
  rekordbox_write.py                     the one place rekordbox's database is written (memory cues and loops)
  eject.py drive_toc.py burn_check.py    ejecting, reading a disc's table of contents, checking a burn
  cli*.py                                the `dubcrate` command-line tool
  ui/                                    the PySide6 windows, themes and the user guide (ui/help/)
scripts/                                 builds, the guide's pictures, and the UI review gallery (ui_snapshots.py)
tests/                                   pytest, run offscreen
spikes/                                  throwaway hardware drivers and measurements
dev_docs/                                hardware findings, formats, plans: start at dev_docs/README.md
```

[AGENTS.md](AGENTS.md) has the module-by-module notes and the rules the code relies on.

## Things to know before changing code

- **Burning uses up real discs.** Everything that can burn defaults to a dry run and
  needs `--yes`. Read [dev_docs/burning.md](dev_docs/burning.md) before touching it.
- **Rekordbox is only ever read, with one exception.** Never write to its analysis files, and never to its database except through `src/dubcrate/rekordbox_write.py`, which writes memory cues only (backup first, read back, undone on failure; see `AGENTS.md` and `dev_docs/rekordbox-cue-writes.md`). Tests use synthetic databases, never a real library.
- **The SQLite library belongs to the GUI thread.** Worker threads compute and
  return results; the GUI thread stores them.
- **Discs reference library track ids.** Anything that can go on a disc must be a library track:
  that is why the Explorer's tracks live in a hidden source rather than a separate list.
- **Tests never open a sound card.** `tests/conftest.py` swaps in a fake device.
- Look at the window in all three looks (dark, light, system6) after changing it.
  `uv run python scripts/ui_snapshots.py` renders **every window, dialog, message and menu** in all three looks to
  `build/ui_snapshots/index.html` (about five minutes; `--quick` for dark only, `--only NAME` for some scenes), marks text
  that doesn't fit, lists every piece of text on each screen, and lets you mark each screen and add notes. A new window or
  dialog needs a scene in `scripts/ui_snapshot_scenes.py` (`tests/test_ui_snapshot_harness.py` fails without one).

## Documentation

Everything below `dev_docs/` is indexed in [dev_docs/README.md](dev_docs/README.md). The ones to know first:

| | |
| --- | --- |
| [AGENTS.md](AGENTS.md) | the module-by-module notes and the hard rules (also for AI coding agents) |
| [dev_docs/command-line.md](dev_docs/command-line.md) | the `dubcrate` command-line tool |
| [dev_docs/burning.md](dev_docs/burning.md) | the burn spike, hardware findings, what is verified |
| [dev_docs/cross-platform.md](dev_docs/cross-platform.md) | Linux and Windows: what is portable and the plan |
| [dev_docs/roadmap.md](dev_docs/roadmap.md) | what is next, decisions made and rough edges (the live to-do list is `TODO.md`) |
| [BUILDING.md](BUILDING.md), [CHANGELOG.md](CHANGELOG.md) | building the app, and the release notes (a draft until the first release) |

## License

Dubcrate is [GPL-3.0-or-later](LICENSE). By contributing you agree your work is
released under the same terms.
