# AGENTS.md

Guidance for AI coding agents working in this repo. `README.md` is for end users;
`CONTRIBUTING.md` has setup and an overview, and `dev_docs/` has the command-line
reference, the burning and hardware notes, and the roadmap.

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
- `src/dubcrate/build.py`: `build_burn_disc` turns a planned disc into rendered WAVs plus a `burn.Disc`, re-checking capacity against the rendered lengths. `disc_problems` lists what blocks a burn. `plan_burn` / `render_plan` are its library-free halves (the burn window renders on a worker), and `media_problems` checks the disc in the drive.
- `src/dubcrate/cli.py`, `cli_discs.py`: the `dubcrate` command (import, sources, nodes, tracks, disc ..., burn) until there is a GUI.
- `src/dubcrate/audio/`: preview playback through miniaudio, free of the library.
  - `devices.py` (output devices, buffer sizes), `decode.py` (any file to 44.1 kHz stereo int16 in memory: miniaudio first, ffmpeg for what it can't read, e.g. M4A/AIFF), `player.py` (`AudioPlayer`: play/pause/seek/volume, and scratching: `begin_scratch` / `scratch_to` / `end_scratch` make the audio thread chase a target the GUI moves, with inertia and the drag's speed estimated from event timestamps (`note_scratch_target`; `render_block` takes an optional `now` clock so tests can simulate mouse timing); the audio thread runs only `render_block`, a pure function over `PlaybackState`, so it is tested with a fake device).
- `src/dubcrate/beatjump.py`: `jump_time`, beat jump along a beat grid (pure). Drag-to-scratch lives in `ui/waveform_view.py` (`DetailView`) and `ui/preview.py`.
- `src/dubcrate/waveform.py`: the 3-band `Waveform` (low blue / mid amber / high white), from rekordbox's bytes or computed from audio (`compute_waveform`), plus the RGB and blue looks derived from the same bands (`rgb_colors`, `whiteness`, `overall_height`, `raster_columns`). `src/dubcrate/analysis.py`: reads rekordbox's ANLZ files (read-only): waveforms, beat grid, cues, phrases.
- `src/dubcrate/ui/`: the PySide6 window (`dubcrate-gui`, or `python -m dubcrate.ui`).
  - `theme.py`, `style.qss.template`, `style_system6.qss.template`, `mono_icons.py`, `icons/`, `fonts/`, `splitter.py`, `track_colors.py` come from the sibling apps (akaiutil-GUI, JV Studio, AKAISDS) so all of them look alike: System / Light / Dark / Macintosh System 6. Dubcrate's own QSS goes under the "Dubcrate additions" marker at the bottom of `style.qss.template`; every colour is a `${key}` from the palettes in `theme.py` (add a key to ALL palettes, or startup raises a KeyError). Custom-painted widgets read `theme.current_palette()`.
  - `player_strip.py` (transport, title/time/key/BPM, volume), `waveform_view.py` (`OverviewView` with phrase bar and cues, `DetailView` scrolling past a fixed playhead with the beat grid), `preview.py` (`PreviewController`: loads a track on a worker, ignores stale loads, owns the player), `settings_dialog.py` (audio device, buffer size, test tone, theme), `track_table.py` (Return adds, Space plays; the Preview column delegate; `transport_key_is_taken`: Space and Left/Right are also handled window-wide by `MainWindow.eventFilter`, except in text fields, sliders and trees, so a new text-entry widget type needs adding there), `wave_style.py` (the waveform's palette, which can differ from the theme's, the chosen look, and `render_columns`: waveform colours come from `wave_style.color`, never straight from the theme).
  - `splash.py` (the startup splash: drawn in code with its own fixed look, never reads the theme; `uv run python -m dubcrate.ui.splash` previews it) and `startup.py` (re-reads imported rekordbox sources on a worker while the splash shows, before the window is built; folders are not re-scanned),
  - `burn_dialog.py` (the burn window: drive and disc check, confirmation, speed, worker-thread burn; tests inject a fake burner and `conftest.py` blocks the real one), `overlaps.py` (the "already used" notice), `help_dialog.py` (Help > User Guide: renders `help/manual.md`; ported from AKAISDS's quick start dialog) and `help/manual.md` (the user guide, checked by `tests/test_help.py`; keep it true when the UI changes).
  - `disc_tree.py` (`DiscTree`: the right-hand tree, with rekordbox's + on the picked row; it only emits `new_disc_requested`, the window creates the disc).
  - `main_window.py` (three panes: the library tree on the left; in the middle a `QStackedWidget` of the track list and the open disc; the disc tree, with folders, on the right), `disc_view.py` (the open disc, and all disc/folder management; the open disc is also the *target* that tracks are added to), `capacity_bar.py` (adapted from akaiutil-GUI's usage bar), `models.py` (track/disc/library-tree and disc-tree models; `DiscTreeModel` does the drag and drop), `tasks.py` (`run_async`).
  - `src/dubcrate/settings.py`: QSettings (theme, window layout, last disc, default disc size and gap).
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
- A disc is a playlist, like any other: there are **no burn records or frozen snapshots**. A burn only sets `disc.last_burned_at`, a note; a burned disc is as editable as any. Discs live in `disc_folder`s (deleting one moves its contents up a level, never deletes a disc).
- What is written on a CD for a track is `DiscEntry.title` / `.artist`: the disc's own edit if any, else the library track's. Edits are stored on the `disc_track` row (they travel with it through add, remove and reorder: the store moves `_Slot`s, never bare track ids) and must never be written to `track`, rekordbox or an audio file. Burning, burn records and problem messages use the entry's text, not `entry.track.title`.

## Conventions

- Python 3.13+, type hints, `pathlib.Path` for paths, stdlib where it will do.
- Run external tools with `subprocess` and an explicit `check=` argument.
- **GUI threading:** the SQLite `Library` belongs to the GUI thread. A `run_async` task may scan, probe or read a rekordbox snapshot, but never touches the library: it returns a result and the GUI-thread callback stores it (`probe_lengths` / `scan_folder` / `read_master_db` are the task-safe pieces).
- **Audio:** the playback callback runs on a native thread that calls into Python, so keep `render_block` light (numpy slicing, no I/O). A device still running at interpreter exit kills the process on macOS (found in AKAISDS), hence the `atexit` guard in `player.py`: keep it. Tests never open a real device (`tests/conftest.py` patches the factory); a real-device check is a manual smoke test with silent audio.
- **Rekordbox analysis is read, never written.** What was checked against real files: the 3-band byte order (low, mid, high), the 150 columns/s detail rate, the beat grid, "high"-mood phrase names, and how rekordbox's own blue and colour waveforms relate to the 3-band data (the RGB and blue looks are derived from that; see `dev_docs/rekordbox-analysis.md`). NOT checked (no sample had them): cue entries (parsed defensively), and the "mid"/"low" mood phrase names (from Deep Symmetry's format notes).
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

## Working notes (from the first sessions)

### Where things stand

Built: library import (folders and rekordbox), disc planning (fit, split, reorder,
exact capacity), the `dubcrate` command-line tool, the burn path (CLI only), and the
PySide6 window with the preview player and waveforms. Burning through DiscRecording
is proven on the owner's hardware (a disc burned and played on a CDJ-1000MK3, CD-TEXT
shown); the gapless fix, the `cdrdao` backend and non-zero gaps are not. **Next:** a
hardware check of the burn window, and the Explorer: `dev_docs/roadmap.md`
has the list, the decisions already made, the known rough edges and what is
unverified. `dev_docs/rekordbox-analysis.md`, `audio.md` and `burning.md` hold what
was measured, and how.

### Shell and tooling gotchas (macOS)

- The Bash tool runs zsh. `sed -i` needs `sed -i ''`; there is no `timeout` command;
  an unmatched glob is an error (`rm -f a b c`, not `rm -f x*`); a variable holding
  several words is not split. When a shell one-liner gets fiddly, use a short Python
  script instead.
- `uv run` uses the project of the **current directory**. Check you are in the Dubcrate
  folder: running it from a sibling repo silently used that repo's environment.
- `ruff format` re-wraps long lines, so an exact-string edit written from memory can
  miss. Read the region first; a script that asserts each match fails loudly.
- Imports of PySide6 at module level mean a plain `python -c` needs
  `QT_QPA_PLATFORM=offscreen` to avoid opening a window.

### The owner's data (read it, never change it)

- `~/Library/Pioneer/rekordbox/` is the **live** rekordbox library. Only read a snapshot
  (`library/rekordbox._snapshot`); never open `master.db` in place or write anything.
- `~/Music/Rekordbox` is the live music folder that rekordbox's paths point to.
  `~/Music/Rekordbox_copy` is a throwaway copy of the music files (about 12 GB, 1,084
  tracks), meant for scanning. Both are read-only to us.
- A real burn uses a blank CD-R the owner loads. Use `--simulate` first, and ask before
  any real burn. Hardware: LiteOn eSAU108 USB burner, two CDJ-1000MK3.
- Try things in a scratch library (`dubcrate --library /tmp/x.db`), never the owner's
  default one.

### Looking at the window

The tests can't tell you whether it looks right. Render it offscreen and read the PNG,
in all three looks (`dark`, `light`, `system6`). Send the settings and the theme's
generated icons to temp locations, as `tests/conftest.py` does, and use a silent
device so nothing plays:

```python
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["DUBCRATE_DATA_DIR"] = "/tmp/dc-shots/data"
from PySide6.QtCore import QSettings

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(
    QSettings.Format.IniFormat, QSettings.Scope.UserScope, "/tmp/dc-shots/settings"
)
from PySide6.QtWidgets import QApplication
from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library
from dubcrate.ui import theme
from dubcrate.ui.main_window import MainWindow


class Silent:  # a stand-in sound card
    def start(self, gen):
        pass

    def stop(self):
        pass

    def close(self):
        pass


app = QApplication([])
app.setOrganizationName("shots")
app.setApplicationName("shots")
theme.apply_to_app(app, "dark")
win = MainWindow(
    Library.open("/tmp/dc-shots/library.db"),
    player=AudioPlayer(device_factory=lambda *a: Silent()),
)
win.resize(1440, 920)
win.show()
app.processEvents()
win.grab().save("/tmp/dc-shots/dark.png")
```

### Reference code in the owner's other apps

They sit beside this repo and share its look and conventions: use them before writing
something new.

- **AKAISDS** (`src/core/audio_preview.py`, the audio section of its settings dialog):
  the model for the preview player. It also has a reusable `Knob` class, if one is ever
  needed. **akaiutil-GUI** (`ui/theme.py`, `usage_bar.py`, `splitter.py`, `tasks.py`):
  where Dubcrate's theme and capacity bar come from. **JV Studio**: the `dev_docs/`,
  `BUILDING.md` and hardware-test-plan style, and the Nuitka / `pysidedeploy` build.
- The theme files are copies, so improvements in one app don't reach the others on their
  own. `diff` against the sibling to port a change; keep Dubcrate's own QSS under the
  "Dubcrate additions" marker.
- akaiutil-GUI's `LICENSE` is GPL **version 2**, Dubcrate's is GPL-3.0-or-later. The
  copied files come from the GPL-3 lineage (their headers say so), but flag it to the
  owner before moving more code across.

### How the owner works

- The owner commits; you may `git add` and should suggest a short commit message.
- Decisions go in `dev_docs/roadmap.md` ("Decisions so far"); keep `README.md` for end
  users only.
