# AGENTS.md

Guidance for AI coding agents working in this repo. `README.md` is for end users;
`CONTRIBUTING.md` has setup and an overview, and `dev_docs/` has the command-line
reference, the burning and hardware notes, and the roadmap.

## Commands

```sh
uv sync                                  # install deps
uv run pytest                            # tests (no hardware needed)
scripts/test_linux.sh                    # the same suite on Linux, in Docker (the CI default leg)
uv run ruff check . && uv run ruff format .
uv run spikes/burn_spike.py doctor       # check tools, drive and media
```

Run ruff and pytest before calling work done. The ruff settings are in `pyproject.toml`
(line length 88, otherwise ruff's own defaults); follow whatever it reports.

## Layout

- `src/dubcrate/cd.py`: Red Book constants, sector maths, CD-TEXT string cleaning.
- `src/dubcrate/tools.py`: `find_tool(name)` (Qt-free; bundled `dubcrate/bin` first, then `PATH`; a `Tool` says its path and whether it is bundled), `missing_tools`, `missing_message`, `describe` / `tool_version` (Settings > Library lists both, the log has them, `smoke_test._check_tools` fails a built app that doesn't bundle both). `ui/tools_dialog.py` is the notice `ui/app.py` shows after start-up if one is missing (every start until found; no "don't show again", no way to choose another ffmpeg).
- `src/dubcrate/render.py`: ffmpeg decode to 16-bit/44.1k stereo WAV, padded to whole sectors.
- `src/dubcrate/burn/base.py`: `Disc`, `DiscTrack`, `BurnOptions`, `Burner` protocol.
- `src/dubcrate/burn/discrecording.py`: macOS backend (PyObjC). Import only on darwin.
- `src/dubcrate/burn/__init__.py`: `default_backend()` (DiscRecording on a Mac, cdrdao elsewhere: the one place that decides, callers never test the platform) and `backend_problem(name)` (why a backend can't run here, in plain words; the burn window shows it up front and never starts a burner then).
- `src/dubcrate/burn/cdrdao.py`: cdrdao backend, TOC generation (`build_toc(..., relative_to=)` for an exported TOC), `find_cdrdao` (through `tools.find_tool`), `install_hint` per OS, `media_info` from `cdrdao disk-info` (`parse_disk_info`), `explain_failure` (cdrdao's output as plain words), `_ProgressFilter` (one log line per 10 percent), the under-4-second refusal, stdin closed. Its output formats come from cdrdao 1.2.4's binary strings and from running it without a drive: **no real drive has been seen**. `dev_docs/linux-test-plan.md` is the first-session checklist.
- `src/dubcrate/loudness.py`: level matching for audio CDs (Qt-free): `measure(path)` (ffmpeg `ebur128`, decoded as `render_track` will: stereo, 44.1 kHz) gives `Loudness(lufs, true_peak_db)`; `gain_for(loud, target)` is ONE fixed gain per track, `target.lufs - lufs`, held to `PEAK_CEILING_DB` (-0.3 dBTP) and `MAX_GAIN_DB` (12) and never negative for lack of headroom; no compression, no limiter, never `loudnorm`/`dynaudnorm` (they change dynamics). A `Target` is a LUFS figure, or `PEAK_TARGET` (`lufs` None): **peak normalising**, which puts the true peak at the ceiling instead (it does not match loudness, it may turn a track down when its peak is over, and `Gain.limited` then means the 12 dB cap held it). `PRESETS` (peak, -9 Loud, -10 Club, -12 Balanced, -14 Gentle; `preset_for(key)`, `nearest_preset(lufs)`) are the only choices the windows offer; `DEFAULT_TARGET_LUFS = -10`, `TARGET_RANGE` (-24 to -6) is for the CLI's `--target`. No louder preset on purpose: nothing but a limiter could reach it. `library/levels.py` measures on a worker (`measure_loudness`, task-safe) and stores (`store_loudness`, `Library.set_loudness`; schema v10 `track.loudness_lufs` / `true_peak_db`, dropped when the file's size changes like `cd_sectors`). `build.apply_levels(plan, measured, target)` puts `PlanTrack.gain_db` on each track and `BurnPlan.levels_note` says what it did (export README and the burn log use it); `render_track(..., gain_db)` applies it as a `volume` filter in double precision before the dither, and a gain never changes a track's length. `build.planned_with_levels` is the library-thread route (CLI `--levels match --target N` or `--levels peak`; takes a `Target`, None = off). The burn window's Levels row (a Normalise tick box and the presets; `_apply_levels`: measures on a worker, `_plan` is `_base_plan` with or without gains, a failed measurement blocks the burn with the way out in the message) and Settings > Burning (a tick box and the same combo: `levels/enabled`, `levels/choice`; `settings.get_levels_target()` gives a `Target` or None; the older `levels/mode` and `levels/target` are still read when the new keys are absent) drive it. MP3 discs are left alone (a gain would mean re-encoding every file). **Not tried on a real disc or a CDJ.**
- `src/dubcrate/iso.py`: `build_iso(folder, volume, out)` makes the ISO 9660 + Joliet image of an MP3 disc by running xorriso / genisoimage / mkisofs (`hdiutil` on a Mac); `IsoError` is an expected error. Used by export (`disc.iso`) and by `CdrdaoBurner.burn_data`.
- `src/dubcrate/export.py`: `export_plan(plan, parent)` writes a disc as files for any burning program (audio: ASCII-named numbered WAVs, `disc.toc`, README; MP3: the disc's folder with the README beside it) into a NEW folder, never overwriting, removing its own half-made folder on failure. `dubcrate export DISC DIR` and the burn window's Export… use it; the window's chooser is `burn_dialog.ask_export_folder` (`conftest.py` refuses it, patch it).
- `src/dubcrate/library/`: Dubcrate's own SQLite library.
  - `models.py`: `TrackRecord`/`NodeRecord` (what a source produces) and `Track`/`Node`/`Source` (what the store returns).
  - `store.py`: `Library`. `replace_source` syncs a source in one transaction and keeps track ids stable across re-imports.
  - `folder.py`: scan a folder of audio files (ffprobe tags); directories become browse nodes.
  - `rekordbox.py`: read a rekordbox 6/7 `master.db` via pyrekordbox. Mapping is in pure functions so it is testable with fake rows.
  - `lengths.py`: `measure_tracks` probes tracks and caches their exact CD length (`track.cd_sectors`).
  - Discs live in the same store (`disc`, `disc_track`): `create_disc`, `add_to_disc` (plain, `fit`, `strict`), `remove_from_disc`, `move_in_disc`, `split_into_discs`.
- `src/dubcrate/mp3.py`: MP3 CDs, pure: sizes in 2048-byte data sectors, size estimates (`track_sectors`, always high: every file is constant bitrate at `cbr_target`, 320 or the source's own lower rate for a lossy one; `scan_mp3` reads frame headers to tell CBR from VBR, and `render_mp3` copies only a CBR MP3 and verifies what it writes), `file_name` (`NN Artist - Title.mp3`), `format_amount` / `format_span` (minutes for an audio disc, MB for an MP3 one). A disc has a `kind` (`audio` or `mp3`, schema v7) and `DiscEntry.sectors` is in the disc's own unit, so the planner, capacity bar and over-length flag work for both; never use `Track.length_sectors` for a disc, use `Track.sectors_on(kind, bitrate)`. Folders on an MP3 disc are `disc_dir` rows and `disc_track.dir_id` (schema v8): positions in `add_to_disc` / `reorder_disc` count within a folder, the limits (`mp3.MAX_FILES_PER_FOLDER`, `MAX_FOLDERS`, `MAX_DEPTH`) are enforced in the store, and the right-hand tree has a `("disc_dir", disc id, folder id)` row for each. `render.render_mp3` writes the files, `build.render_plan` stages them as a `DataDisc`, and `Burner.burn_data` writes it (`cdrdao` can't).
- `src/dubcrate/labels.py`: index labels for a case, pure (no Qt), for audio and MP3 discs: `build_labels` spreads a disc's entries over `LabelOptions.per_disc` labels (0 = Auto: the fewest that keep text at `AUTO_MIN_MM`, or fit the chosen `font_pt`) in as few of `LabelOptions.columns` columns as will do (rows of number, title, artist and the optional right-hand columns `SIDE_COLUMNS`: rounded BPM, key in the chosen notation and `Track.duration` as M:SS, each with its own `show_*` option, duration off by default; the painter draws whichever have something on the label; text is the `DiscEntry`'s, never the library track's). Given an MP3 disc's `dirs`, folders become `FolderRow` headings (root first, then depth first by name, numbered per folder in disc order like `build.render_plan`'s file names), kept whole on a label where readable, else cut with a repeated `(cont.)` heading; a heading is never the last line of a label or column. Search order in `build_labels`: fewest labels, then fewest columns, then folders kept together. `sheet_for` / `paginate` pack labels from several discs onto pages of `PAGE_SIZES` (A3, A4, A5, A6, Letter, Legal, portrait, millimetres). `settings.get_label_options` remembers the choices. `ui/label_paint.py` draws one page for a `QImage` preview, a `QPrinter` or a `QPdfWriter` with the same code (device pixels from millimetres; the system font, never the theme's), and `ui/label_dialog.py` is the window (its disc picker is `disc_tree`, a `QTreeWidget` of the Discs list's folders, `_disc_items()` in the order shown; ticking a folder ticks what is inside it, `_on_item_changed` plans the labels once; the heading printed on a label is still `disc_paths`' "Folder / Disc"), and `ui/label_preview.py` its preview: a scroll area that paints only the visible part of the page through `label_paint.page_image(..., region)` at the screen's physical dpi (retina included), with Fit or 25% to 400% zoom (the box's steps, Ctrl+wheel steps, or a trackpad pinch: a native `ZoomNativeGesture` on the viewport, smooth, around the pointer; the pinch is NOT tried on a real trackpad, only with synthetic events), and drag to pan. The message labels under the settings keep their height always, so nothing moves when a warning appears. `ask_printer` / `ask_pdf_path` are its only real dialogs; `conftest.py` refuses them.
- `src/dubcrate/planning.py`: pure packing algorithms (`fit_tracks`, `split_sequential`) on lists of sector lengths.
- `src/dubcrate/build.py`: `build_burn_disc` turns a planned disc into rendered WAVs plus a `burn.Disc`, re-checking capacity against the rendered lengths. `disc_problems` lists what blocks a burn. `plan_burn` / `render_plan` are its library-free halves (the burn window renders on a worker), and `media_problems` checks the disc in the drive.
- `src/dubcrate/logsetup.py`: the log file (Qt-free), `dubcrate.log` in `~/Library/Logs/Dubcrate` (`DUBCRATE_LOG_DIR` overrides; the tests set it). `dubcrate-gui` calls `setup`, `install_exception_hooks` (uncaught errors, on any thread, get their traceback in the log), `enable_crash_log` (`crash.log`, faulthandler) and `log_startup`; `--verbose` adds DEBUG. Every module logs with `logging.getLogger(__name__)`. Help > Show Log File opens the folder. `ui/dialog_log.py` logs every `QMessageBox` shown (installed only by `app.py`, never in tests).
- `src/dubcrate/cli.py`, `cli_discs.py`: the `dubcrate` command (import, sources, nodes, tracks, disc ..., burn) until there is a GUI.
- `src/dubcrate/audio/`: preview playback through miniaudio, free of the library.
  - `devices.py` (output devices, buffer sizes), `decode.py` (any file to 44.1 kHz stereo int16 in memory: miniaudio first, ffmpeg for what it can't read, e.g. M4A/AIFF), `player.py` (`AudioPlayer`: play/pause/seek/volume, and scratching: `begin_scratch` / `scratch_to` / `end_scratch` make the audio thread chase a target the GUI moves, with inertia and the drag's speed estimated from event timestamps (`note_scratch_target`; `render_block` takes an optional `now` clock so tests can simulate mouse timing); the audio thread runs only `render_block`, a pure function over `PlaybackState`, so it is tested with a fake device).
- `src/dubcrate/beatjump.py`: `jump_time`, beat jump along a beat grid (pure); with no grid and no tempo `PreviewController.beat_jump` jumps `FALLBACK_JUMP_SECONDS` (2 s). Drag-to-scratch lives in `ui/waveform_view.py` (`DetailView`) and `ui/preview.py`.
- `src/dubcrate/waveform.py`: the 3-band `Waveform` (low blue / mid amber / high white), from rekordbox's bytes or computed from audio (`compute_waveform`), plus the RGB and blue looks derived from the same bands (`rgb_colors`, `whiteness`, `overall_height`, `raster_columns`). `src/dubcrate/analysis.py`: reads rekordbox's ANLZ files (read-only): waveforms, beat grid, cues, phrases.
- `src/dubcrate/ui/`: the PySide6 window (`dubcrate-gui`, or `python -m dubcrate.ui`).
  - `theme.py`, `style.qss.template`, `style_system6.qss.template`, `mono_icons.py`, `icons/`, `fonts/`, `splitter.py`, `track_colors.py` come from the sibling apps (akaiutil-GUI, JV Studio, AKAISDS) so all of them look alike: System / Light / Dark / Macintosh System 6. Dubcrate's own QSS goes under the "Dubcrate additions" marker at the bottom of `style.qss.template`; every colour is a `${key}` from the palettes in `theme.py` (add a key to ALL palettes, or startup raises a KeyError). Custom-painted widgets read `theme.current_palette()`.
  - `player_strip.py` (transport, title/time/key/BPM, the painted transport buttons `transport_button.py`, the settings cog `gear_button.py` and the volume `knob.py`, a small port of AKAISDS's `Knob`; none use image files), `waveform_view.py` (`OverviewView` with phrase bar and cues, `DetailView` scrolling past a fixed playhead with the beat grid), `preview.py` (`PreviewController`: loads a track on a worker, ignores stale loads, owns the player; `MainWindow._step_track` is Previous / Next and `_play_on_after_end` (on `player.finished`) plays on to the next track of the list or open disc, skipping missing files, stopping after the last, and only from a track that is in the list on show), `settings_dialog.py` (audio device, buffer size, theme), `track_table.py` (Return adds, Space plays; the Preview column delegate; `transport_key_is_taken` (Esc in the search box, a `SearchBox` in `main_window.py`, moves focus to the list on show: `leave_search`; Return does the same and picks its first row, `pick_first`, applying a search still waiting for its pause first): Space and Left/Right are also handled window-wide by `MainWindow.eventFilter`, except in text fields, sliders and trees, so a new text-entry widget type needs adding there), `wave_style.py` (the waveform's palette, which can differ from the theme's, the chosen look, and `render_columns`: waveform colours come from `wave_style.color`, never straight from the theme).
  - `status_bar.py` (`AppStatusBar`: "Dubcrate v0.1.0" at the left, the blue "Update available" button beside it fed by `UpdateChecker.on_available`, then the messages: it keeps its own message label because a QStatusBar hides its left widgets while a message shows; the resize grip is off. Its button colours are the `update_*` palette keys, in all three palettes),
  - `spinbox_drag.py` (ported from AKAISDS): one application-level event filter, installed in `app.py` after the theme, that makes every spin box drag like a knob (Shift: finer) and select-all on click so a typed number replaces it. It never consumes a press, only a drag's moves and release. Tests install it themselves (`tests/test_ui_spinbox_drag.py`); a new spin box needs nothing. AKAISDS's `wheel_guard` (wheel only after a click) is not ported. Not felt by hand yet (trackpad feel unknown).
  - `full_width_tabs.py` (`FullWidthTabBar`, ported from jv-studio / AKAISDS: the tabs of a `QTabWidget` share its whole width to the pixel; `tabs.setTabBar(FullWidthTabBar(tabs))` BEFORE adding tabs. Document mode is NOT the way: Qt then paints no pane frame. The Settings window uses it).
  - `splash.py` (the startup splash: drawn in code with its own fixed look, never reads the theme; `uv run python -m dubcrate.ui.splash` previews it) and `startup.py` (re-reads imported rekordbox sources on a worker while the splash shows, before the window is built; folders are not re-scanned),
  - `burn_dialog.py` (the burn window: drive and disc check, confirmation, speed, worker-thread burn; tests inject a fake burner and `conftest.py` blocks the real one), `overlaps.py` (the "already used" box: Add Anyway / Skip Duplicates / Cancel through `ask_overlap`, which tests patch at `disc_view.ask_overlap`; `conftest.py` refuses it otherwise), `help_dialog.py` (Help > User Guide: renders `help/manual.md`; ported from AKAISDS's quick start dialog) and `help/manual.md` (the user guide, checked by `tests/test_help.py`; keep it true when the UI changes).
  - `disc_tree.py` (`DiscTree`: the right-hand tree, with rekordbox's + on the picked row in its own last column; it only emits `new_disc_requested`, the window creates the disc). Discs and folders are named in place, never in a dialog: a new one is created with a free default name and `MainWindow.edit_in_tree` opens its editor; `DiscTreeModel.setData` only emits `renamed`, and the window applies it on a deferred call (rebuilding the tree inside the editor's own commit crashes Qt).
  - `source_tabs.py` (`SourceTabBar`: the painted vertical tab strip down the left of the window, Library, Explorer and one tab per pinned folder (`remove_tab`, `context_menu_requested`; a `movable` tab can be dragged among the movable ones, `move_tab`, `tabs_reordered`; the buttons sit in a scroll area with no scroll bar and edge chevrons, so many tabs never raise the window's minimum height), from the palette, System 6 inverted; `current()` is right inside a `current_changed` listener) and `explorer.py` (`ExplorerTree`: Music, home and `mounted_volumes()` (macOS `/Volumes` minus the boot-disk link and `com.apple.*` folders such as Time Machine's local snapshots; Linux and Windows have helpers, NOT tried) as roots, folders only, each read when expanded; `folder_selected(path)`; `ExplorerTree(watch=volume_parents())` watches the folders where a drive appears (`QFileSystemWatcher`, 600 ms settle, then `refresh_roots`; Windows has none, so it waits for the tab; a picked folder on a vanished drive leaves nothing picked and emits nothing, `_syncing`)). **The Explorer's tracks are real library tracks in a hidden source** (`source.hidden`, schema v9; `store.EXPLORER_PATH`): `Library.sync_directory(dir, tracks)` upserts one folder (ids stable, matched on path; a track that left is dropped unless a disc uses it, then marked stale), `explorer_node(dir)` finds its list, `prune_explorer()` (called by `app.py` at start-up) forgets what no disc uses. `sources()` and "All Tracks" (`_track_query` without a source or node) leave the hidden source out. `folder.scan_directory(dir, known)` reads only the files directly inside a folder and skips probing a file whose size is unchanged. In `MainWindow` the middle list's pick is `_selection`: a library-tree item, or `("explorer", folder)`; the library tree keeps its own pick in `_library_selection` (`_on_tree_current_changed` only applies it while the Library tab shows), and the folder is shown at once from what is stored, then re-read on a worker and re-synced on the GUI thread (`_explorer_scanned`). **Pinned folders** (`settings.get_pinned_folders`, `explorer/pinned`; tab key `pin:<path>`; `MainWindow.pin_folder` / `unpin_folder` / `_add_pinned_tab`): each is an `ExplorerTree(contents_of=folder)`, whose top level is the folder's subfolders (no row for the folder: the tab is named for it); `current_path()` is the folder while nothing is picked, a click on empty space or `select_path(folder)` picks it again (`_pick_contents_folder`), and right-click on empty space is the menu of the folder (`_menu_path`), with the same hidden-source tracks and `_selection == ("explorer", folder)`, re-read each time its tab is opened (`_on_left_tab`). Not recursive. Right-click in any `ExplorerTree` offers Pin / Unpin and Refresh (`pin_requested`, `unpin_requested`, `refresh_requested`; `refresh_roots` / `refresh_folder` add and remove rows without touching the ones that stay, and the Explorer tab calls `refresh_roots` whenever it is opened); right-click on a pinned tab offers Move Up / Move Down / Unpin, and a pinned tab can be dragged (`MainWindow._on_tabs_dragged` keeps `_pins` in the strip's order). PySide gotcha: `QStandardItem.insertRow(row, item)` loses the item, so pass `[item]`. The same file under two rows (rekordbox's and the Explorer's) is tied together by `store.path_key` (NFC + casefold of the path, no schema change): `Library.rekordbox_twin(track)`, `track_usage` (a track is used when its file is on a disc under any row), `same_file_ids` (the ● / ○ marks in `_sync_disc_markers`), and `sync_directory` gives an Explorer file rekordbox's BPM and key (tags fill blanks only). `PreviewController.load` uses the twin's `analysis_path` and rekordbox root, so such a file gets rekordbox's waveform and beat grid. No file is ever matched on name or content, only path.
  - `main_window.py` (three panes: the library tree on the left; in the middle a `QStackedWidget` of the track list and the open disc; the disc tree, with folders, on the right), `disc_view.py` (the open disc, and all disc/folder management; the open disc is also the *target* that tracks are added to), `capacity_bar.py` (adapted from akaiutil-GUI's usage bar), `models.py` (track/disc/library-tree and disc-tree models; `build_source_model` gives a library with ONE source no row for it, its folders and playlists follow "All Tracks" directly, so `find_selection(model, ("source", id))` finds nothing then and `MainWindow.selected_source_id()` treats "All Tracks" as that source for Re-import / Remove; with several sources each keeps its row; `DiscTreeModel` does the drag and drop), `tasks.py` (`run_async`).
  - `src/dubcrate/settings.py`: QSettings (theme, window layout, last disc, default disc size and gap, key notation, waveform look, burn speed, beat jump, update-check switches, the label window's options (`get_label_options`, `labels/*`), and the left tab, last Explorer folder and pinned folders).
- `src/dubcrate/updates.py`: the update check (GitHub Releases, no Qt, `urlopen` injectable). `ui/update_check.py` (`UpdateChecker`: runs it on a worker and applies the policy: you asked = say everything, start-up = only news, once a day, skipped versions stay quiet), `ui/update_dialog.py`, `ui/about_dialog.py` (About, and the third-party notices from `ui/help/THIRD_PARTY_NOTICES.md`), `ui/version.py` (the version and the icon: a built app reads `dubcrate/_build_version.py`, which the build script writes from the git tag and does not track).
- `src/main.py`, `src/dubcrate/ui/smoke_test.py`: the entry that `pyside6-deploy` compiles, and `dubcrate-gui --smoke-test=FILE`, which CI runs against every built app (it never touches your settings, library, rekordbox or a drive).
- `scripts/` (`build_macos.sh`, `build_ffmpeg_macos.sh`, `make_icons.sh`, `dmg_settings.py`), `assets/dmg/`, `.github/workflows/` (`test.yml`: lint and tests on every push; `build.yml`: on a `v*.*.*` tag, tests, then the Apple Silicon and Intel builds, then a draft release), `BUILDING.md`: packaging, modelled on AKAISDS and JV Studio. `tests/test_build_files.py` checks that the script, the DMG layout and the workflows agree.
- `scripts/make_screenshots.py` + `screenshot_recipes.py` + `demo_music.py`: regenerate the guide's and README's pictures (`src/dubcrate/ui/help/screenshots/`, offscreen at 2x; about a minute; `--list`, `--only NAME`, `--overwrite`, `--out DIR`, `--scheme`). One `@shot` recipe per picture. They are taken from a MADE-UP collection (`demo_music.py` synthesises MP3s with invented titles, artists, BPM and key tags into a temp folder) imported into a temp library, with the Explorer pointed at a pretend home folder and no drives: nothing of the owner's is read, and the player has a silent sound card and the burn window a stand-in burner. Staged: the track list's Preview column is filled in (the app only fills it from rekordbox analysis; a plain folder shows a waveform once a track is loaded), and a folder import has no phrases, cues or beat grid. The label picture is the window composited over the main window (`_over`: a dialog is a window of its own, so `window.grab()` alone leaves it out). `tests/test_make_screenshots.py` fails if the guide names a picture with no recipe, a recipe has no file, or a file is named by neither the guide nor the README. **Change the UI -> re-run `uv run python scripts/make_screenshots.py --overwrite` and look at the PNGs.** A new picture: add a recipe, run it, reference it from `manual.md`.
- `src/dubcrate/cdj_card.py`: the CDJ-1000MK3's SD card (`PIONEER/CDJ/*.PDJ`: cues, loops, hot cues, a waveform per played track), pure, read AND write: `IndexFile`, `TrackFile`, `CueEntry`, `HotCue`, `WaveRecord`, `DiscId`, `read_card`, `CardFormatError`. **A disc's ID is its TOC** (track count, lead-out, track 2 start, last track start, in absolute frames, track 1 at 150): `DiscId.from_toc` / `toc_from_lengths` compute it from planned track lengths, and a cue's position is the track's start plus the time in it. Measured from a real card and checked byte for byte (`tests/test_cdj_card.py`, real files in `tests/data/cdj_card/`), **but no file written here has been loaded by a player yet**, and nothing in the app uses it. `dev_docs/cdj1000mk3-card.md` is the reference: the format, the experiments, and **"Gaps, and where more testing is indicated"**: read it before changing the module or trusting a field. Never write to the owner's real card; the card images in `~/cdj-card/` are theirs (kept outside the repo), take new ones with `spikes/cdj_card_snap.sh NAME [/dev/diskN]` (sudo, so the owner runs it) and read them with `spikes/cdj_card_dump.py dump|diff`.
- `src/dubcrate/card_sync.py`: planning a sync onto the CDJ card, pure: `map_cues` (rekordbox `Cue`s per track -> `WantedCue`s at card positions), `merge_cues` (never changes what is on the card; skips a cue within `DUPLICATE_FRAMES` = 2 of one there; reports past `MAX_CUES` as `overflow`, never drops silently), `plan_card` (`DiscRequest`s -> a `CardPlan` with a `Status` per disc and `writes()` for `cdj_card.write_card`). The owner's rules: **every rekordbox cue becomes a memory cue; the three hot cue slots are never written or touched**; sync lists only Dubcrate's own audio discs. `card_discs.py` is the library half (`prepare_disc`: a disc's TOC from its measured tracks, `rekordbox_cues` through the rekordbox twin like the preview, the reasons a disc can't be synced), `card_report.py` words for a card and a plan, and `cli_card.py` is `dubcrate card show | backup | sync DISC... --card PATH [--yes]` (a dry run unless `--yes`, backs up first, never touches hot cues). The Sync tab is not built (`dev_docs/card-sync.md` is the plan and the hardware checklist); **no written file has been loaded by a player**. `spikes/cdj_card_testimage.py` builds a test image (a COPY with a disc's cues set) and `spikes/cdj_card_restore.sh` writes an image back to a card (the owner runs it: sudo, and it erases the card).
- `spikes/`: throwaway hardware drivers. Reusable logic belongs in the package.
- `tests/`: pytest. Tests must not need a drive, a disc or the network (`conftest.py` blocks all three).
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
- A disc is a playlist, like any other: there are **no burn records or frozen snapshots**. A burn only sets `disc.last_burned_at`, a note; a burned disc is as editable as any. Discs live in `disc_folder`s. Deleting a `disc_folder`, or a folder on an MP3 disc (`disc_dir`), **deletes what is in it** (discs and folders; tracks come off the disc), at any depth; the interface asks first unless it is empty. Deleting never touches a library track or a music file.
- What is written on a CD for a track is `DiscEntry.title` / `.artist`: the disc's own edit if any, else the library track's. Edits are stored on the `disc_track` row (they travel with it through add, remove and reorder: the store moves `_Slot`s, never bare track ids) and must never be written to `track`, rekordbox or an audio file. Burning, burn records and problem messages use the entry's text, not `entry.track.title`.

## Conventions

- Python 3.13+, type hints, `pathlib.Path` for paths, stdlib where it will do.
- Run external tools with `subprocess` and an explicit `check=` argument.
- **Errors:** a failure a user can cause or that is outside our control (offline, a private repo, no drive, a bad file) is an *expected* error: raise a plain-language exception (`TaskError` from a worker, after translating, as `ui/update_check.py` does with `UpdateCheckError`) so the task layer logs one WARNING line and the UI shows the message. Anything else is a bug and is logged with its traceback. Never let a raw `HTTPError`/`OSError` reach a dialog.
- **Subprocess output:** read it with `encoding="utf-8", errors="replace"`, never `text=True` alone: that decodes with the system's code page, which on Windows turned ffprobe's UTF-8 tags into mojibake. Likewise pass `encoding="utf-8"` to `read_text` / `open` in tests and code.
- **GUI threading:** the SQLite `Library` belongs to the GUI thread. A `run_async` task may scan, probe or read a rekordbox snapshot, but never touches the library: it returns a result and the GUI-thread callback stores it (`probe_lengths` / `scan_folder` / `read_master_db` are the task-safe pieces).
- **Audio:** the playback callback runs on a native thread that calls into Python, so keep `render_block` light (numpy slicing, no I/O), and keep long GIL holds off the GUI thread: a full garbage collection (30 to 45 ms) or a long paint is a crackle in the preview (`ui/app.py: freeze_garbage_collector` freezes the start-up objects out of the collector and `tune_gil_for_audio` sets the GIL's switch interval to 0.2 ms, because the default 5 ms is longer than a 128-frame buffer; `spikes/audio_jitter.py` measures it; `dev_docs/audio.md`). A device still running at interpreter exit kills the process on macOS (found in AKAISDS), hence the `atexit` guard in `player.py`: keep it. Tests never open a real device (`tests/conftest.py` patches the factory); a real-device check is a manual smoke test with silent audio.
- **Rekordbox analysis is read, never written.** What was checked against real files: the 3-band byte order (low, mid, high), the 150 columns/s detail rate, the beat grid, "high"-mood phrase names, and how rekordbox's own blue and colour waveforms relate to the 3-band data (the RGB and blue looks are derived from that; see `dev_docs/rekordbox-analysis.md`). NOT checked: the "mid"/"low" mood phrase names (from Deep Symmetry's format notes). **rekordbox's cues are in `master.db`'s `djmdCue`, not the analysis files** (those have none for a local library, so the preview's cue markers show none): `library/rekordbox.py: read_cues` reads them from a snapshot, `Kind` 0 memory / 1, 2, 3 hot A, B, C as read off a real library (5 and the many memory cues near 0 s are not understood), and `card_discs.RekordboxCues` finds a track's, through its rekordbox twin.
- **GUI tests** delete every window after each test (`conftest.py: _no_windows_left_behind`) and turn off `quitOnLastWindowClosed`: left alone, the windows piled up to over a thousand and each theme change restyled all of them (0.04 s early, 11 s late: the suite took 25 minutes on Windows CI, now under two on a Mac). Do not rely on a window outliving its test. They run offscreen (`tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen`, and sends QSettings and the theme's generated icons to a temp dir via `DUBCRATE_DATA_DIR`). Patch `QMessageBox` / `QInputDialog` / `QFileDialog` rather than letting a test open a modal dialog. `wait_for(predicate)` spins the event loop for worker results. To eyeball a change, render the window with `window.grab().save("x.png")` and look at the PNG, in all three looks (dark, light, system6).
- The ChiKareGo2 font (System 6 theme) is CC BY: keep `ui/fonts/README.txt` and the credit in the About dialog.
- Store ids (`Track.id`) are what discs will reference, so re-imports must keep them
  stable: match on `ext_id`, never delete-and-reinsert tracks.
- Dubcrate is `GPL-3.0-or-later` (`LICENSE`). Keep dependencies light, and keep them
  GPL-3 compatible (permissive, LGPL, or GPL-2.0-or-later / GPL-3.0). Code from rbxport
  (GPL-2.0-or-later) could be used, but no tag library is needed: `ffprobe` reads tags.
- GUI will be PySide6 and packaged with Nuitka. Avoid dependencies that make
  Nuitka bundling hard (SQLCipher bindings are the known risk).
- Bundled binaries (ffmpeg, ffprobe) are run as subprocesses, never linked, and must be LGPL
  builds (`scripts/build_ffmpeg_macos.sh`: no `--enable-gpl` / `--enable-nonfree`, LAME linked in
  statically; the script refuses a GPL build). Always find them with `tools.find_tool` (the bundle
  `dubcrate/bin`, then `PATH`), never `shutil.which` or a bare name; a missing one is
  `render.ToolMissingError`, an expected error. `extend_tool_path` in `ui/app.py` adds Homebrew's
  folders to `PATH` only for the fallback (Finder gives an app a minimal `PATH`). `cdrdao` is not bundled.
- **The only network use is the update check** (`updates.py`, once a day at start-up
  unless switched off in Settings, or when asked). Keep it that way, and keep what it
  sends to a request for GitHub's release list.
- **Add an entry to `ui/help/THIRD_PARTY_NOTICES.md` when a dependency or third-party
  code, data or art is added** (`tests/test_notices.py` fails if a runtime package is
  missing). Run `scripts/build_macos.sh` and its smoke test after adding a dependency:
  a module Nuitka can't see only shows up in the built app.

## Cross-platform

Dubcrate burns on macOS only. Linux and Windows have groundwork (a per-OS burner choice, drive lists, CI legs marked `experimental`, Export), all **[not tried]** on those systems. **Read `dev_docs/cross-platform.md` before touching burners, tool
lookup (`extend_tool_path`), app or log paths, packaging or CI**: it records what is portable, the
decision to use `cdrdao` for audio discs on Linux and Windows, and the phased plan. Keep platform
branches in one named helper each (as `rekordbox.default_master_db` does), never scattered
`sys.platform` tests, and tag anything you could not run on that OS as not tried.

## Hardware notes

- Test rig: macOS, LiteOn eSAU108 USB burner, a pair of CDJ-1000MK3s.
- macOS labels the drive "Unsupported", but DiscRecording reports it can write
  CD-R, CD-TEXT, SAO and test-write.
- 80 min = 360,000 sectors, 74 min = 333,000. Use 10 to 16x for old players; this drive's slowest speed is 10x.

## Working notes (from the first sessions)

### Where things stand

*Last updated after the session that added play-through, the normalise presets (incl. peak), the audio crackle fixes, the label picker's folders, flat pinned and Library trees, and the search box's Esc / Return.*

Built: library import (folders and rekordbox), disc planning (fit, split, reorder,
exact capacity), audio **and MP3 discs** (MP3 folders included), the `dubcrate` command-line
tool, the burn path (CLI, and the burn window), the PySide6 window with the preview player,
waveforms and scratching, an update check, a built `.dmg` (Nuitka), and since the last handover:

- **Index label printing** (`labels.py`, `ui/label_dialog.py`, `label_paint.py`, `label_preview.py`):
  A3 to A6, Letter, Legal; spread over N labels (Auto), up to three columns, MP3 folders as
  headings, optional BPM / key / duration columns, font size, copies, a zoomable sharp preview
  (box, Ctrl+wheel, trackpad pinch, drag), print or PDF. *Disc > Print Labels…* (⌘P).
- **Drag-to-change spin boxes** everywhere (`ui/spinbox_drag.py`, ported from AKAISDS).
- A faint **zero line** in the zoomed waveform.
- **The left side is tabs: Library, Explorer and one per pinned folder** (`ui/source_tabs.py`, `ui/explorer.py`; schema
  v9). The Explorer browses folders and drives without importing (a drive plugged in while the app runs
  appears by itself); its tracks live in a hidden source. A pinned folder's tab and a single-source Library list
  what is inside, with no row for the folder or source itself.
- **Audio:** play-through (a track that ends starts the next), the crackle fixes (`freeze_garbage_collector`,
  `tune_gil_for_audio`: see `dev_docs/audio.md`), level matching with presets and a peak-to-full-scale choice.

Burning through DiscRecording is proven on the owner's hardware (a disc burned and played on a
CDJ-1000MK3, CD-TEXT shown); the burn *window*, the gapless fix, the `cdrdao` backend, non-zero gaps,
MP3 discs and **everything below are not proven on hardware or a real display**.

**Not tried by hand (tests and offscreen renders only):** printing on a real printer or paper (and
whether the Mac print panel keeps the paper size we ask for), trackpad pinch and the feel of
dragging spin boxes, the Explorer on a real Mac (permission prompts for Documents / Downloads / drives,
a slow or unplugged drive), and whether the built app bundles QtPrintSupport (`scripts/build_macos.sh`).

**Next** (the owner's `TODO.md` is the live list and is theirs: do not tidy it; `dev_docs/roadmap.md` has the reasons):
1. Hardware checks of the burn window, an MP3 disc, level matching on a CDJ and the "not tried" list above.
2. **A tab per app library** (not one merged Library tab), when a second library exists. Only
   rekordbox is used (Engine DJ and Algoriddim djay are backups synced from it with Lexicon, so there
   is nothing to read from them). **Mixxx** is wanted, but the owner uses it on Linux, not on this Mac:
   wait for a real `mixxxdb.sqlite` to check against before writing a reader.
3. Release preparation (signing, the README, a changelog), then Linux and Windows.

**Decisions to keep:** no BPM / key / waveform *analysis* of files (loudness is the one measurement taken, at burn time only, cached, never shown as a library column, because it is baked into the audio the CDJ plays) (the CDJ-1000 can't use that data;
tags are only read, blank when missing; the waveform is drawn from the audio at preview time); the
Explorer never offers the whole system disk; discs reference library track ids, so anything that can go
on a disc must be a library track (hence the hidden Explorer source). `dev_docs/rekordbox-analysis.md`,
`audio.md` and `burning.md` hold what was measured, and how.

### Shell and tooling gotchas (macOS)

- The Bash tool runs zsh. `sed -i` needs `sed -i ''`; there is no `timeout` command;
  an unmatched glob is an error (`rm -f a b c`, not `rm -f x*`); a variable holding
  several words is not split. When a shell one-liner gets fiddly, use a short Python
  script instead.
- `uv run` uses the project of the **current directory**. Check you are in the Dubcrate
  folder: running it from a sibling repo silently used that repo's environment.
- `ruff format` re-wraps long lines, so an exact-string edit written from memory can
  miss. Read the region first; a script that asserts each match fails loudly.
- Do not leave a helper script whose name matches a standard-library module (`wave.py`, `json.py`)
  in the folder you run scripts from: Python imports it instead of the real one (a stray `wave.py`
  that built a `QApplication` once broke every later script). Keep scratch scripts in the scratch
  folder with distinctive names.
- The window tests that read a folder (`tests/test_ui_explorer.py`) stand in for `ffprobe` by patching
  `dubcrate.library.folder.probe`, and for the length measuring by patching
  `dubcrate.ui.disc_view.probe_lengths`; copy that when a test needs files but not ffmpeg. A test that
  winds a library back to an older schema must undo every later migration too (`tests/test_mp3_discs.py`).
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

To see the status bar's "Update available" button without a release (the repository is private, so a
real check finds nothing): `uv run scripts/preview_update_button.py` (`--theme`, `--version`, `--dialog`,
`--screenshot PNG`). It tells the real update checker it found a release; nothing is fetched or saved.

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
