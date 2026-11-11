# Roadmap

## Next

1. **Burn from the window.** A confirmation step ("is a blank CD-R loaded?"), a
   progress log, drive and speed choice, and running DiscRecording off the GUI thread.
   This needs a hardware check (see [burning.md](burning.md)). The burn path
   in `build.py` and the backends already exist; only the dialog is missing.
2. **A record of what was burned.** Each burn saves a snapshot (tracks, lengths,
   titles, date, drive, speed). A *Duplicate* action makes an editable copy to
   re-burn. Open question: should a disc lock once burned, or stay editable?
3. **The source rail** from the rbxport-style layout: Library, Explorer (a live
   folder browser, no import needed) and Discs (the list of planned and burned
   discs, with the disc currently in the burner so a finished burn can be read back
   and checked).
4. **Printable track list** for the disc sleeve, since old CDJs have no screen for it.

## Later

- MP3 data discs as a secondary mode (ISO 9660 + Joliet; an old CDJ loads audio CDs
  faster, so audio CD stays the default).
- Rekordbox XML export as an import path with no SQLCipher dependency (a fallback if
  `pyrekordbox` ever lags behind a rekordbox update).
- Windows and Linux burn backends behind the existing `Burner` interface.
- Music/iTunes as a source (decided to skip for now; it needs an exported library XML
  and can't give us files for streamed or protected tracks).

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
