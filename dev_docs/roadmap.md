# Roadmap

## Next

1. **Burn from the window.** A confirmation step ("is a blank CD-R loaded?"), a
   progress log, drive and speed choice, and running DiscRecording off the GUI thread.
   Show what the inserted disc holds (`Burner.media_info`, `build.media_problems`) and
   check against it, as the CLI already does.
   This needs a hardware check (see [burning.md](burning.md)). The burn path
   in `build.py` and the backends already exist; only the dialog is missing.
2. **A record of what was burned.** *Store side done* (`record_burn`, `burns`,
   `duplicate_burn`, schema v3), the CLI saves a record after a real burn, and the
   disc panel has a *Planned | Burned* switch with *Duplicate* and *Delete*. Still to
   do: save a record from the burn dialog once it exists, then `show_burned()`.
3. **The source rail** from the rbxport-style layout: Library, Explorer (a live
   folder browser, no import needed) and Discs (the list of planned and burned
   discs, with the disc currently in the burner so a finished burn can be read back
   and checked).
4. **Printable track list** for the disc sleeve, since old CDJs have no screen for it.

5. **Help menu.** `src/dubcrate/ui/help/manual.md` is written; it needs a viewer dialog
   (port akaiutil-GUI's `manual_dialog.py`) and a *Help > User Guide* item. Screenshots
   still to take (`![...](screenshots/x.png)`; `tests/test_help.py` checks images exist).
   Update the guide when the burn dialog lands: its "Burning a disc" section is CLI-only.

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
