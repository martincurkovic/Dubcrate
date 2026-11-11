# AGENTS.md

Guidance for AI coding agents working in this repo. See `README.md` for what the
project is.

## Commands

```sh
uv sync                                  # install deps
uv run pytest                            # tests (no hardware needed)
uv run ruff check . && uv run ruff format .
uv run spikes/burn_spike.py doctor       # check tools, drive and media
```

Run ruff and pytest before calling work done. The ruff config comes from outside
the repo; follow whatever it reports.

## Layout

- `src/dubcrate/cd.py`: Red Book constants, sector maths, CD-TEXT string cleaning.
- `src/dubcrate/render.py`: ffmpeg decode to 16-bit/44.1k stereo WAV, padded to whole sectors.
- `src/dubcrate/burn/base.py`: `Disc`, `DiscTrack`, `BurnOptions`, `Burner` protocol.
- `src/dubcrate/burn/discrecording.py`: macOS backend (PyObjC). Import only on darwin.
- `src/dubcrate/burn/cdrdao.py`: cdrdao backend, TOC generation.
- `src/dubcrate/library/`: Dubcrate's own SQLite library.
  - `models.py`: `TrackRecord`/`NodeRecord` (what a source produces) and `Track`/`Node`/`Source` (what the store returns).
  - `store.py`: `Library`. `replace_source` syncs a source in one transaction and keeps track ids stable across re-imports.
  - `folder.py`: scan a folder of audio files (ffprobe tags); directories become browse nodes.
  - `rekordbox.py`: read a rekordbox 6/7 `master.db` via pyrekordbox. Mapping is in pure functions so it is testable with fake rows.
  - `lengths.py`: `measure_tracks` probes tracks and caches their exact CD length (`track.cd_sectors`).
  - Discs live in the same store (`disc`, `disc_track`): `create_disc`, `add_to_disc` (plain, `fit`, `strict`), `remove_from_disc`, `move_in_disc`, `split_into_discs`.
- `src/dubcrate/planning.py`: pure packing algorithms (`fit_tracks`, `split_sequential`) on lists of sector lengths.
- `src/dubcrate/build.py`: `build_burn_disc` turns a planned disc into rendered WAVs plus a `burn.Disc`, re-checking capacity against the rendered lengths. `disc_problems` lists what blocks a burn.
- `src/dubcrate/cli.py`, `cli_discs.py`: the `dubcrate` command (import, sources, nodes, tracks, disc ..., burn) until there is a GUI.
- `src/dubcrate/audio/`: preview playback through miniaudio, free of the library.
  - `devices.py` (output devices, buffer sizes), `decode.py` (any file to 44.1 kHz stereo int16 in memory: miniaudio first, ffmpeg for what it can't read, e.g. M4A/AIFF), `player.py` (`AudioPlayer`: play/pause/seek/volume; the audio thread runs only `render_block`, a pure function over `PlaybackState`, so it is tested with a fake device).
- `src/dubcrate/waveform.py`: the 3-band `Waveform` (low blue / mid amber / high white), from rekordbox's bytes or computed from audio (`compute_waveform`). `src/dubcrate/analysis.py`: reads rekordbox's ANLZ files (read-only): waveforms, beat grid, cues, phrases.
- `src/dubcrate/ui/`: the PySide6 window (`dubcrate-gui`, or `python -m dubcrate.ui`).
  - `theme.py`, `style.qss.template`, `style_system6.qss.template`, `mono_icons.py`, `icons/`, `fonts/`, `splitter.py`, `track_colors.py` come from the sibling apps (akaiutil-GUI, JV Studio, AKAISDS) so all of them look alike: System / Light / Dark / Macintosh System 6. Dubcrate's own QSS goes under the "Dubcrate additions" marker at the bottom of `style.qss.template`; every colour is a `${key}` from the palettes in `theme.py` (add a key to ALL palettes, or startup raises a KeyError). Custom-painted widgets read `theme.current_palette()`.
  - `player_strip.py` (transport, title/time/key/BPM, volume), `waveform_view.py` (`OverviewView` with phrase bar and cues, `DetailView` scrolling past a fixed playhead with the beat grid), `preview.py` (`PreviewController`: loads a track on a worker, ignores stale loads, owns the player), `settings_dialog.py` (audio device, buffer size, test tone, theme), `track_table.py` (Return adds, Space plays; the Preview column delegate), `wave_style.py`.
  - `main_window.py` (sources tree, track table, disc panel), `disc_panel.py`, `capacity_bar.py` (adapted from akaiutil-GUI's usage bar), `models.py` (track/disc/source-tree models), `tasks.py` (`run_async`).
  - `src/dubcrate/settings.py`: QSettings (theme, window layout, last disc).
- `spikes/`: throwaway hardware drivers. Reusable logic belongs in the package.
- `tests/`: pytest. Tests must not need a drive or a disc.
- `assets/icon/`: `AppIcon.png` is the 1024x1024 master; `AppIcon.txt` holds the
  icon.kitchen link it came from. Platform icons (.icns, .ico) get derived from
  the master at build time.

## Hard rules

- **Never burn without explicit user confirmation.** Burning consumes a physical
  disc. Spike commands default to a dry run; only pass `--yes` when the user has
  said to burn and has a blank CD-R loaded.
- **Git is handled by a human.** Committing, pushing, branching and merging are
  where code review happens, so they are not the agent's job. An agent may run
  `git add` to stage its work. When a commit is appropriate, suggest a brief
  commit message and stop there; don't run `git commit`, `git push`, `git reset`
  or anything else that changes history or the remote.
- **Never write to a rekordbox database or library.** Reading is fine, and only
  from a snapshot copy (`rekordbox._snapshot`), never the live `master.db`.
  Dubcrate stores its own data in its own SQLite DB.
- **Treat the user's music as read-only.** Scanning and probing only; never
  modify, move or delete audio files. Use `--library <scratch path>` when trying
  the CLI so the user's real app-data library isn't touched.
- Burners are backends behind the `Burner` protocol. Don't leak backend details
  (DiscRecording types, cdrdao flags) into callers.
- Tracks are gapless by default (`BurnOptions.pregap_sectors = 0`). DiscRecording
  would otherwise insert 2 s of silence before every track. Capacity checks must
  count any pregap via `Disc.used_sectors`.
- A track's length for capacity purposes is `Track.length_sectors`: measured if known,
  else a conservative estimate (rekordbox durations are whole seconds). Rendered length
  is authoritative, and `build_burn_disc` writes it back. Never plan with raw `duration`.
- Re-imports must not silently drop a track a disc uses: it is kept and marked `stale`.
- Every track must be a whole number of sectors (588 samples) before it reaches a
  burner. `render_track` guarantees this; keep it that way.
- CD-TEXT is required, not optional. Text goes through `cdtext_clean` first.

## Conventions

- Python 3.13+, type hints, `pathlib.Path` for paths, stdlib where it will do.
- Run external tools with `subprocess` and an explicit `check=` argument.
- **GUI threading:** the SQLite `Library` belongs to the GUI thread. A `run_async` task may scan, probe or read a rekordbox snapshot, but never touches the library: it returns a result and the GUI-thread callback stores it (`probe_lengths` / `scan_folder` / `read_master_db` are the task-safe pieces).
- **Audio:** the playback callback runs on a native thread that calls into Python, so keep `render_block` light (numpy slicing, no I/O). A device still running at interpreter exit kills the process on macOS (found in AKAISDS), hence the `atexit` guard in `player.py`: keep it. Tests never open a real device (`tests/conftest.py` patches the factory); a real-device check is a manual smoke test with silent audio.
- **Rekordbox analysis is read, never written.** What was checked against real files: the 3-band byte order (low, mid, high), the 150 columns/s detail rate, the beat grid, and "high"-mood phrase names. NOT checked (no sample had them): cue entries (parsed defensively), and the "mid"/"low" mood phrase names (from Deep Symmetry's format notes).
- **GUI tests** run offscreen (`tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen`, and sends QSettings and the theme's generated icons to a temp dir via `DUBCRATE_DATA_DIR`). Patch `QMessageBox` / `QInputDialog` / `QFileDialog` rather than letting a test open a modal dialog. `wait_for(predicate)` spins the event loop for worker results. To eyeball a change, render the window with `window.grab().save("x.png")` and look at the PNG, in all three looks (dark, light, system6).
- The ChiKareGo2 font (System 6 theme) is CC BY: keep `ui/fonts/README.txt` and the credit in the About dialog.
- Store ids (`Track.id`) are what discs will reference, so re-imports must keep them
  stable: match on `ext_id`, never delete-and-reinsert tracks.
- Dubcrate is `GPL-3.0-or-later` (`LICENSE`). Keep dependencies light, and keep them
  GPL-3 compatible (permissive, LGPL, or GPL-2.0-or-later / GPL-3.0). Code from rbxport
  (GPL-2.0-or-later) could be used, but no tag library is needed: `ffprobe` reads tags.
- GUI will be PySide6 and packaged with Nuitka. Avoid dependencies that make
  Nuitka bundling hard (SQLCipher bindings are the known risk).
- Bundled binaries (ffmpeg, cdrdao) are run as subprocesses, never linked. An
  eventual bundled ffmpeg must be an LGPL build.

## Hardware notes

- Test rig: macOS, LiteOn eSAU108 USB burner, a pair of CDJ-1000MK3s.
- macOS labels the drive "Unsupported", but DiscRecording reports it can write
  CD-R, CD-TEXT, SAO and test-write.
- 80 min = 360,000 sectors, 74 min = 333,000. Use 10 to 16x for old players; this drive's slowest speed is 10x.
