# Roadmap

## Next

1. **Burn from the window.** *Built, not yet tried on hardware.* `ui/burn_dialog.py`
   shows the drive and what the inserted disc holds, asks to confirm a blank CD-R,
   takes a speed (default from Settings), can do a test-only burn, logs progress, and
   marks the disc as burned. It renders and burns on a worker thread, which needs a hardware check
   (see [burning.md](burning.md)). Still to do: a drive picker (it uses the first
   drive), and a way to cancel.
2. **Read a finished burn back.** With the burner attached, read the disc in the drive and
   check it against the disc it was burned from (the old idea of a burn record became
   "a disc just remembers when it was burned").
3. **The Explorer** in the left tree. *Built (step 1), not yet looked at by the owner on a real Mac.*
   The left pane is a vertical tab strip (`ui/source_tabs.py`) over a stacked page: **Library**
   (as before) and **Explorer** (`ui/explorer.py`: Music, home and plugged-in drives, never the
   whole system disk; folders read when opened). Its tracks are real library tracks in a hidden
   source, so discs, sorting, search and previews all work unchanged (`Library.sync_directory`,
   `prune_explorer`; schema v9). **Next, as agreed with the owner:**
   - **Pinned folders**: *Built, not yet looked at on a real Mac.* Right-click a folder in the
     Explorer > Pin Folder gives it a tab below Explorer without leaving the current screen (Unpin Folder on the tab or the tree);
     the folder, not recursive, is read again each time its tab is opened (no background watching).
     Tabs reorder by dragging (only pinned ones; Library and Explorer stay first and second) or by
     Move Up / Move Down in the tab's menu, and the strip scrolls (wheel, no scroll bar, edge chevrons,
     the picked tab scrolled into view) when there are more pins than fit. Explorer: opening the tab lists
     the drives again, and right-click > Refresh lists a folder again. Not tried on a real Mac or a
     trackpad: the feel of dragging a tab and of the scrolling.
     The owner's use: a folder of misc MP3s that isn't in rekordbox.
   - **A tab per app library** (not one merged Library tab), for when a second library exists. Only
     rekordbox is in use today: Engine DJ and Algoriddim djay are backups synced from rekordbox
     (the master) through Lexicon, so there is nothing to read from them. **Mixxx** (the owner uses
     it on Linux, not on this Mac) is the one wanted: its database is plain SQLite; check a real
     `mixxxdb.sqlite` before writing the reader, and give the readers a small common interface
     (tracks, folders, optional analysis).
   - **No analysis**: BPM and key are only read from tags (blank when there are none), because the
     CDJ-1000 can't use them on a disc. Waveforms are computed from the audio when there is no
     rekordbox analysis.
   - *Done, not yet tried on real files:* a file that is also in the imported rekordbox library
     (matched on its path, `store.path_key`: case and Unicode form folded) takes rekordbox's BPM and
     key at `sync_directory` time, previews with rekordbox's waveform / beat grid / cues
     (`Library.rekordbox_twin`, used by `PreviewController.load`), and counts as used when its twin is
     on a disc (`track_usage`; the ● / ○ marks use `same_file_ids`). Not done: title, artist and the
     rest still come from the tags; a borrowed BPM stays until the file changes if the track later
     leaves rekordbox; no path mapping for a library moved to another folder or drive.
   - The arrow keys jump a fixed 2 s (`preview.FALLBACK_JUMP_SECONDS`) for a track with no beat grid
     and no tempo.
4. **Printable track list** for the disc sleeve, since old CDJs have no screen for it.
   *Built, not yet tried on a printer* (checked with PDFs and offscreen renders). `labels.py`
   is the pure part (splitting a disc over N labels or Auto, columns, folders of an MP3 disc,
   text size, optional BPM / key / duration columns, packing labels from several discs onto pages
   of A3/A4/A5/A6/Letter/Legal), `ui/label_paint.py` draws a page for the preview, a printer or a
   PDF, `ui/label_preview.py` is the zoomable preview (draws only what shows, at the screen's real
   pixels), and `ui/label_dialog.py` is the window (Disc > Print Labels…, ⌘P). Still to do: a real
   print on A4 and Letter sticker paper (does the print panel keep the paper we asked for? how much
   of the edge is unprintable?), check the built app bundles QtPrintSupport (`scripts/build_macos.sh`
   and its smoke test), cm / inch units for the sizes (the owner's TODO), a way to print straight
   after a burn, and the album or year on a label if wanted. Auto picks the fewest labels that keep
   the text at 6 pt or more (`AUTO_MIN_MM`); long titles are cut with "…", never wrapped.

5. **Help menu.** *Done:* *Help > User Guide* opens `ui/help_dialog.py`, which renders
   `ui/help/manual.md` (find in page, working contents links, callout boxes, images that
   fit the window). Still to do: take screenshots for the guide
   (`![...](screenshots/x.png)`; the tests check any image paths exist), and look at the
   window on a real display. The guide's "Burning a disc" section must be kept in step
   with the burn window.

## Later

- Logging follow-ups (the log file itself is done: `logsetup.py`): a Settings switch for verbose logging (only `--verbose` today), logging inside the library store and the CLI, and a dialog for an uncaught exception (it is only logged).

- MP3 CDs: *built, never burned.* A disc is an audio CD or an MP3 CD (`disc.kind`, schema
  v7). See Decisions, and [burning.md](burning.md) for the hardware check. Still to do: split
  across MP3 discs from the window (the CLI's `disc split --mp3` does), moving a folder to
  another place on the disc (folders can be made, renamed and deleted; `delete` moves things
  up), a way to pick the kind
  in the + button, burning an MP3 disc with the cdrdao backend, and whatever the first
  real test shows about the CDJ.
- Rekordbox XML export as an import path with no SQLCipher dependency (a fallback if
  `pyrekordbox` ever lags behind a rekordbox update).
- Windows and Linux burn backends behind the existing `Burner` interface: the plan, the
  decision to use `cdrdao`, and what is unverified are in [cross-platform.md](cross-platform.md).
- Music/iTunes as a source (decided to skip for now; it needs an exported library XML
  and can't give us files for streamed or protected tracks).

## Decisions so far

- The layout is three panes under the player: the library's file tree on the left, the track list (or the open disc) in the middle, and the **Discs** list on the right.
- A **disc is a playlist**: in the right-hand list, filed in folders, opened in the middle by clicking it (which also makes it the target for adds). Burned or not makes no difference, and there are **no frozen burn snapshots**: a disc only remembers when it was last burned. Dragging tracks onto a disc adds them to it (rekordbox-style); discs and folders drag into folders. Folders are listed first, A to Z, then discs A to Z; deleting a folder deletes what is in it (its discs and folders, at any depth; the tracks stay in the library), after asking unless it is empty. That replaced "moves its contents up a level", at the owner's request. (Schema v6 converted old burn records: a burned disc got `last_burned_at`, and a burn of a deleted disc became a disc.)
- Double-click previews a track; Return (or the button, or dragging) adds it to a disc.
- Music/iTunes is skipped for now. The theme is shared with the owner's other apps.
- Licensed GPL-3.0-or-later. No tag library: `ffprobe` reads tags.
- Disc size and the gap between tracks are *Settings > Discs* defaults for **new**
  discs (80 min, gapless), not controls on the panel. What counts at burn time is the
  disc in the drive (`Burner.media_info`); the planned size is only a guide.
- A planned disc stays editable after a burn. Each burn saves its own frozen snapshot
  (`burn` / `burn_track`, copies of title, artist, length and path), so editing or
  deleting the disc, or removing its source, never changes the record. *Duplicate*
  makes a new editable disc from a snapshot and skips tracks no longer in the library.
- Adding a track that is already on the disc, on another planned disc, or on a past
  burn asks first (Add Anyway / Skip Duplicates / Cancel), listing where it is used (`Library.track_usage`,
  `ui/overlaps.py`). Simulated burns don't count. Splitting into discs doesn't check yet.
- Waveform looks (3-band, RGB, blue) are chosen in Settings or under View > Waveform. RGB
  and blue are derived from the 3-band data so every track has them (measured against
  rekordbox's own, see `rekordbox-analysis.md`). The waveform background follows the theme
  (light in the light theme); *Always dark* keeps rekordbox's dark one. System 6 always draws
  the 3-band look.
- Dragging the zoomed waveform scratches (hold the track, play it at the drag's speed, backwards
  too, silence when still; release resumes a playing track and leaves a paused one paused).
  Clicking it no longer seeks: the overview does that. Left and right arrows beat jump from the
  track list, by a Settings > Audio size (default 4 beats), along the beat grid, in phase.
- The track list shows only the essential columns by default; the other rekordbox fields (album, year,
  star rating, My Tags, label, remixer, composer, comment, plays, date added, bitrate, sample rate,
  format, path) are under right-click > More Columns, off until chosen. Which are visible is stored as a
  list (`tracks/visible_columns`); a layout saved by the older "hidden columns" setting is carried over.
  Read from rekordbox: `LabelName`, `RemixerName`, `ComposerName`, `MyTagNames`, `DJPlayCount`,
  `StockDate` (checked on the real library; colour is empty on every track there, so it is not offered).
  Existing libraries need one re-import to fill the new fields (schema v4 adds them empty).
- The disc list shows only what a CD holds: # / Time / Artist / Title (no BPM or key). The CD's title
  is the disc's name. Artist and title are editable *for that disc only* (`disc_track.title/artist`,
  schema v5, NULL = use the library's); they feed the burn, the burn record and *Duplicate*, and never
  touch the library, rekordbox or the file. Blank, or the track's own text, clears an edit.
  `dubcrate disc text DISC POS --title/--artist` does the same from a terminal.
- On startup a splash screen (`ui/splash.py`, drawn in code with its own look, modelled on Cobalt's) shows while
  `ui/startup.py` re-reads any **rekordbox** source already imported, on a worker, before the main window
  is built. Folders are not re-scanned (an ffprobe per file is too slow for a launch), a first run imports
  nothing, and a missing, locked or unreadable library is skipped with a status-bar note. A rekordbox that
  reports no tracks is not believed (it would otherwise empty the library). `--no-import` and
  *Settings > Library* turn it off. It always re-reads (no "unchanged since last time" shortcut): the
  schema-v4 fields would otherwise stay empty until rekordbox changed.
- The README is for end users only; developer notes live here and in `CONTRIBUTING.md`.

- **MP3 CDs.** The kind is fixed per disc (`disc.kind`, `mp3_bitrate`) and can be changed
  with *Make MP3 CD / Make Audio CD*, because a disc's tracks are only measured differently:
  `DiscEntry.sectors` is CD audio sectors for an audio disc and 2048-byte data sectors for an
  MP3 one, so capacity, fit, split and the over-length flag work the same for both and
  `capacity_sectors` (360,000 for 80 minutes, 703 MB) is shared. A track that is already MP3
  is copied (tags rewritten from the disc's text, cover art left off), anything else is
  encoded constant-bitrate at the disc's rate. Files are `NN Artist - Title.mp3` in a folder
  named for the disc (the volume label), written ISO 9660 + Joliet by DiscRecording's folder
  track. The planner estimates sizes conservatively; the staged files' real sizes
  (plus `FILESYSTEM_OVERHEAD_SECTORS`) are what the burn checks. Adding past an audio CD's
  length asks once per disc whether to make it an MP3 CD.
- **MP3 bitrates.** Always constant bitrate; the disc's setting (`mp3_bitrate`, Settings > Discs)
  is a ceiling, 320 by default. Sizes on an MP3 disc are estimates (shown with a ~) because
  whether a file is copied or re-encoded is only known by reading it; the staged files are
  measured at burn time. Caching a scan per track would make the common case (CBR copies) exact.
- **Folders on an MP3 disc.** `disc_dir` rows (a tree per disc) and `disc_track.dir_id`; the
  disc's global `position` order stays, and a folder's order is the relative order of its tracks.
  `add_to_disc` and `reorder_disc` take a `dir_id` and count positions *within* that folder; the
  Discs list shows a disc's folders as its children, and a selected folder opens its disc scoped
  to it (`DiscView.set_disc(disc, dir)`), so the track list shows one folder while the capacity bar
  keeps the whole disc. Selections are `("disc_dir", disc id, folder id)`.
- **Clicking.** The trees open on `clicked` as well as on a change of the current row, because
  clicking the row that is already current is not a change for Qt (the old bug: a disc already
  picked in the Discs list, or a library item already picked on the left, needed a click elsewhere
  and back).

- **Labels.** One label option set is remembered (`labels/*` in QSettings); per-disc spread is Auto by
  default (an older saved number is kept). Text is the disc's own title and artist (`DiscEntry`), never
  the library track's. BPM, key and duration each have a box; duration is `Track.duration` as M:SS (the
  library's Time column), off by default. An MP3 disc's folders are headings, numbered per folder like the
  burned file names; the search order is fewest labels, then fewest columns, then folders kept whole.
  "Places already used" (skipping spots on a part-used sheet) was built and then removed at the owner's
  request.
- **The Explorer and the left tabs.** The left pane is a vertical tab strip over pages: **Library** (the
  imported sources, unchanged) and **Explorer**. Tracks the Explorer reads are stored as library tracks in
  a hidden source (`source.hidden`, schema v9), synced one folder at a time (`sync_directory`) and
  pruned at start-up except those on discs: this keeps discs, sorting, search and previews unchanged
  and ids stable. Each app's library gets its **own tab** (owner's choice, over one merged Library tab)
  when a second one is added; pinned folders get tabs too, refreshed when opened. No file analysis:
  BPM and key are tags only.
- **Drag-to-change number boxes** everywhere, via one application-level event filter
  (`ui/spinbox_drag.py`); remove its line in `ui/app.py` to undo.

## Known rough edges
- The Explorer re-reads a folder (ffprobe per new or changed file) on the first visit of each
  session, because what it read is forgotten at start-up (`prune_explorer`). A cache across sessions,
  keyed by path, size and modified time, would fix that. It shows only the files directly inside a
  folder (not recursive), does not watch folders, and a drive plugged in while the app is open needs
  the tree reloaded (`ExplorerTree.reload_roots`, not wired to anything yet).
- The same song in rekordbox and in an Explorer folder is two track rows, tied together only by
  their path (`path_key`). A copy of the file in another folder, or a rekordbox library that points at
  another drive's path, is not recognised.
- AKAISDS's `wheel_guard` (a control answers the wheel only after a click) was not ported with the
  drag-to-change spin boxes, so scrolling over a spin box in a scrolling page can still change it.

- Imports and measuring can't be cancelled, and there is no per-file progress detail.
- A folder scan runs at about 120 files a second (ffprobe per file), so 50,000 files
  would take several minutes. `tinytag` (MIT) would be the faster fallback.
- A computed waveform isn't saved, so a folder track's Preview strip reappears only
  after it has been previewed again in a new session (rekordbox tracks read theirs
  from rekordbox's own files). Caching them in SQLite would fix that.
- The waveform scaling (gamma, the overview's auto-gain) was tuned by eye against the
  rbxport screenshot, not measured.
- `spikes/burn_spike.py` duplicates some of what `build.py` and the CLI now do; it can
  go once the window burns.
- The GUI's *Between tracks* menu offers 1-3 s, whose unit is unverified (see
  [burning.md](burning.md)).

## Unverified

- **Label printing on real hardware**: only PDFs and offscreen renders so far. Not checked: a real
  printer on A4 or Letter sticker paper, whether the Mac print panel overrides the page size, the
  printable edge, and how the small text prints. The zoomable preview's **trackpad pinch** (a native
  `ZoomNativeGesture` on the viewport) was tested only with synthetic events, as was drag-to-pan and
  Ctrl+wheel. The look of the labels on screen was checked in the light and dark themes, not System 6.
- **Drag-to-change spin boxes** (ported from AKAISDS): only simulated mouse events; the feel with a
  trackpad is unknown (the tunables are at the top of `ui/spinbox_drag.py`).
- **The Explorer on a real Mac**: macOS permission prompts for Documents, Downloads, Desktop and
  external drives, a slow or unplugged drive, and large folders (it probes with 8 workers). Tests use a
  stand-in for `ffprobe` and temporary folders; the three themes were rendered offscreen.

- The built app (`scripts/build_macos.sh`). Checked on the owner's Apple Silicon Mac: it builds in about
  two minutes with a warm cache, its `--smoke-test` passes (styles, font, guide, notices, DiscRecording and
  SQLCipher load, window builds) even with Finder's minimal `PATH`, the signature verifies and the DMG holds
  the app, the Applications shortcut, the background and the layout. NOT checked: opening it from Finder by
  hand, the DMG window's look on a real display, the Intel build, the GitHub workflows (never run; the
  repository is private), and whether DiscRecording can reach the drive from the bundled app (a hardware
  check). The first smoke run caught a real fault (`_cffi_backend` missing); a new dependency can do the same.
- The update check against real GitHub: `updates.py` is tested against a fake. While the repository is private
  GitHub answers 404, so *Check for Updates* reports a failure and the start-up check stays silent.
- The splash on a real display and from a built app: a true splash window doesn't take focus on macOS, so
  from source it is an ordinary frameless window (`activating`) and in a built app a real splash. Its own
  `background: transparent` stylesheet (so the theme's can't fill its corners) is a precaution: offscreen
  the corners stay transparent without it, so no test depends on it. Preview it with
  `uv run python -m dubcrate.ui.splash`.

- How the scratch *sounds* on a real output device. It is pitch-shifting by design (speed is
  pitch, like vinyl; no time-stretch). A first version made a steady drag swing between 0.7x
  and 1.4x, because mouse events (60 a second) and audio blocks (43) don't line up; that was
  found by simulation and fixed with a head that has inertia plus the drag's speed estimated
  from the events, so the speed now stays within a few percent at 30, 60 and 120 Hz mice and
  several buffer sizes (`tests/test_audio.py`). That is measured on simulated timing and the fake
  device, not heard. Known: the head runs on by roughly 50 ms of audio when the hand stops
  dead (a block plays audio from the near future, so the hand's position is predicted). The
  16x speed cap and the fade to silence for a still hand were chosen by reasoning. Try it with
  a small buffer in Settings > Audio.
- Rekordbox's own default beat jump size and which keys it uses: 4 beats on the arrow keys is
  the assumption, and the size is a setting because of it.
- Beat jump only works from the track list; with focus elsewhere (the source tree, the search
  box) the arrow keys keep their usual meaning there.

- The RGB and blue looks and the light waveform on a real display (checked offscreen, and
  against rekordbox's data only statistically). Reading rekordbox's `PWV3` / `PWV5` directly
  is the way to exact matches for rekordbox tracks.

- Cue points: parsed defensively but none of the sampled tracks had any, so the
  display is tested only with fake data.
- Phrase names for "mid" and "low" mood tracks (from Deep Symmetry's format notes;
  only "high" mood is checked, and it is 278 of 300 sampled tracks).
- Rekordbox 6 libraries (only 7 has been tried).
- The window run natively on a real display, and previews heard through real speakers
  (only offscreen rendering and a silent device smoke test so far).
- Mouse drag-and-drop in the window (the drop handling is tested directly, not with a
  real drag).
- Everything in [burning.md](burning.md) marked **[not tried]**.

## Before making the repository public

- [ ] Put the ruff settings in `pyproject.toml`. They currently come from a config
      outside the repository, so a contributor would get different results.
- [x] Packaging: Nuitka build script and a macOS DMG like the sibling apps
      (`scripts/build_macos.sh`, [BUILDING.md](../BUILDING.md)). Still open: code signing
      and notarisation, and whether the built app can use the drive (a hardware check).
- [ ] Decide how ffmpeg is supplied. Recommended: bundle a minimal decode-only LGPL build
      of ffmpeg and ffprobe per architecture (a few MB each, built in CI without
      `--enable-gpl`, shipped with its licence and build configuration), looked up before
      `PATH`. Homebrew's is GPL and can't be copied in. `cdrdao` is not needed on macOS.
      For now the app finds Homebrew's.
- [x] Continuous integration: `.github/workflows/test.yml` (lint and tests on macOS, every
      push) and `build.yml` (tag: tests, builds, smoke tests, draft release). Unproven
      until the first run on GitHub (the repository is private).
- [ ] README: screenshots, real download and install steps, and a re-check that every
      claim in it is something that has been tried.
- [ ] Re-read the README's trademark wording and the "not affiliated" notice.
- [x] A third-party notices file, shown from About and the Help menu
      (`ui/help/THIRD_PARTY_NOTICES.md`). Worth a careful read by the owner before release:
      it names each licence and project but does not reproduce the full licence texts.
- [ ] Look through the git history for anything personal that shouldn't be public.
- [ ] A changelog. (The version, from the git tag, is shown in *About* and on the splash,
      and the update check reads GitHub's release notes.)
- [ ] Decide whether `AGENTS.md` and `CLAUDE.md` stay in the public repo.
