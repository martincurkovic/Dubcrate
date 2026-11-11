# Roadmap

## Next

1. **Burn from the window.** *Built, not yet tried on hardware.* `ui/burn_dialog.py`
   shows the drive and what the inserted disc holds, asks to confirm a blank CD-R,
   takes a speed (default from Settings), can do a test-only burn, logs progress, and
   saves a record. It renders and burns on a worker thread, which needs a hardware check
   (see [burning.md](burning.md)). Still to do: a drive picker (it uses the first
   drive), and a way to cancel.
2. **A record of what was burned.** *Store side done* (`record_burn`, `burns`,
   `duplicate_burn`, schema v3), the CLI saves a record after a real burn, and the
   disc panel has a *Planned | Burned* switch with *Duplicate* and *Delete*. The burn
   window saves a record after a real burn and shows it under *Burned*.
3. **The source rail** from the rbxport-style layout: Library, Explorer (a live
   folder browser, no import needed) and Discs (the list of planned and burned
   discs, with the disc currently in the burner so a finished burn can be read back
   and checked).
4. **Printable track list** for the disc sleeve, since old CDJs have no screen for it.

5. **Help menu.** *Done:* *Help > User Guide* opens `ui/help_dialog.py`, which renders
   `ui/help/manual.md` (find in page, working contents links, callout boxes, images that
   fit the window). Still to do: take screenshots for the guide
   (`![...](screenshots/x.png)`; the tests check any image paths exist), and look at the
   window on a real display. The guide's "Burning a disc" section must be kept in step
   with the burn window.

## Later

- MP3 data discs as a secondary mode (ISO 9660 + Joliet; an old CDJ loads audio CDs
  faster, so audio CD stays the default).
- Rekordbox XML export as an import path with no SQLCipher dependency (a fallback if
  `pyrekordbox` ever lags behind a rekordbox update).
- Windows and Linux burn backends behind the existing `Burner` interface.
- Music/iTunes as a source (decided to skip for now; it needs an exported library XML
  and can't give us files for streamed or protected tracks).

## Decisions so far

- The disc panel stays to the right of the track table; the player runs across the top.
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
- [ ] Packaging: Nuitka build scripts and a macOS DMG like the sibling apps, code
      signing and notarisation, and an `Info.plist` that works for the drive access.
- [ ] Decide how ffmpeg is supplied: bundle a build (and satisfy its license, with a
      source offer if it is a GPL build) or keep requiring a Homebrew install.
- [ ] Continuous integration that runs the tests on macOS.
- [ ] README: screenshots, real download and install steps, and a re-check that every
      claim in it is something that has been tried.
- [ ] Re-read the README's trademark wording and the "not affiliated" notice.
- [ ] A third-party notices file for the bundled font and the dependencies
      (all are GPL-3 compatible: MIT, BSD, zlib, MPL-2.0, PySide6's LGPL).
- [ ] Look through the git history for anything personal that shouldn't be public.
- [ ] A version number and changelog, and the version shown in *About*.
- [ ] Decide whether `AGENTS.md` and `CLAUDE.md` stay in the public repo.
