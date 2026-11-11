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
3. **The Explorer** in the left tree: a live folder browser next to the Library (no
   import needed).
4. **Printable track list** for the disc sleeve, since old CDJs have no screen for it.

5. **Help menu.** *Done:* *Help > User Guide* opens `ui/help_dialog.py`, which renders
   `ui/help/manual.md` (find in page, working contents links, callout boxes, images that
   fit the window). Still to do: take screenshots for the guide
   (`![...](screenshots/x.png)`; the tests check any image paths exist), and look at the
   window on a real display. The guide's "Burning a disc" section must be kept in step
   with the burn window.

## Later

- Logging follow-ups (the log file itself is done: `logsetup.py`): a Settings switch for verbose logging (only `--verbose` today), logging inside the library store and the CLI, and a dialog for an uncaught exception (it is only logged).

- MP3 data discs as a secondary mode (ISO 9660 + Joliet; an old CDJ loads audio CDs
  faster, so audio CD stays the default).
- Rekordbox XML export as an import path with no SQLCipher dependency (a fallback if
  `pyrekordbox` ever lags behind a rekordbox update).
- Windows and Linux burn backends behind the existing `Burner` interface.
- Music/iTunes as a source (decided to skip for now; it needs an exported library XML
  and can't give us files for streamed or protected tracks).

## Decisions so far

- The layout is three panes under the player: the library's file tree on the left, the track list (or the open disc) in the middle, and the **Discs** list on the right.
- A **disc is a playlist**: in the right-hand list, filed in folders, opened in the middle by clicking it (which also makes it the target for adds). Burned or not makes no difference, and there are **no frozen burn snapshots**: a disc only remembers when it was last burned. Dragging tracks onto a disc adds them to it (rekordbox-style); discs and folders drag into folders. Folders are listed first, A to Z, then discs A to Z; deleting a folder moves its contents up a level. (Schema v6 converted old burn records: a burned disc got `last_burned_at`, and a burn of a deleted disc became a disc.)
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
  burn asks "Add anyway?" first, listing where it is used (`Library.track_usage`,
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

## Known rough edges

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
