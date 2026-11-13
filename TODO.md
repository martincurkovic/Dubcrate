# TODO List

- [ ] Auto download update (maybe once code signing in place...)
- [x] Linux burn support
  - [x] Export disc as files (WAVs + TOC, or MP3 folder + ISO)
  - [x] cdrdao backend (drive info, errors, progress, MP3 discs via ISO)
  - [ ] Try on a real Linux machine + drive (dev_docs/linux-test-plan.md)
- [ ] Windows burn support
  - [ ] Spike on a real Windows machine (cdrdao + CD-TEXT)
- [ ] Linux build script (AppImage / Flatpak / uv tool?)
- [ ] Windows build script and installer
- [ ] Mixxx library support
- [x] Audio normalisation setting?
  - [ ] Try on a real disc + CDJ (waveform height, any clipping, is -10 LUFS right?)
- [ ] Engine DJ library support
- [ ] Serato library support
- [x] Bundle dependencies (ffmpeg, et. al)
  - [ ] Linux / Windows (they use the system ffmpeg for now)
- [x] CI test legs for Linux, macOS, Windows
  - [ ] Drop `experimental` from the Windows leg in test.yml once happy
- [ ] Run build.yml in CI (bundled ffmpeg, smoke test, does PySide6 import on macOS 15?)
- [ ] Hardware checks on the Mac: burn window, MP3 disc
- [ ] Homebrew packaging once first release public
- [x] Screenshot staging
  - [ ] Maybe use some real songs, not fake ones
- [ ] Clean up user facing docs (readme, user guide)
- [ ] SD card waveform, hotcue, memory cue, loop point reverse engineering
- [ ] SD card sync metadata tab
- [ ] Clean up long text labels in settings and add cm/in units (maybe in print dialog too)
  - [ ] Check on real paper (and say if the vertical line spacing should shrink too)

## More from the roadmap and dev_docs

### Hardware checks (Mac and the CDJ-1000MK3)

- [ ] Confirm a burned disc is really gapless (tracks at 00:02.00, 00:42.00, 01:12.00 in `drutil toc`) now that the 2 s pregap fix is in ([burning.md](dev_docs/burning.md)).
- [ ] Find out what unit `DRPreGapLengthKey` uses before trusting the 1, 2 and 3 s "Between tracks" options ([burning.md](dev_docs/burning.md)).
- [ ] Compare the drive's reported free blocks on a 74 and an 80 minute disc (expect about 333,000 and 359,850) to confirm the capacity check ([burning.md](dev_docs/burning.md)).
- [ ] Run a Test Only burn from the burn window first, to prove DiscRecording is happy being driven from a worker thread ([burning.md](dev_docs/burning.md)).
- [ ] Burn a small MP3 disc and check on the CDJ its load time, file order, what it shows (ID3 or file name) and whether ffmpeg's Info frame bothers it (`-write_xing 0` is the fallback) ([burning.md](dev_docs/burning.md)).
- [ ] Try MP3 disc folders on the CDJ, starting with two nested folders, then the 999-file and 8-level limits and whether `MAX_DEPTH` should be 9 ([burning.md](dev_docs/burning.md)).
- [ ] Burn a nearly full MP3 disc to check the size estimate and `FILESYSTEM_OVERHEAD_SECTORS` ([burning.md](dev_docs/burning.md)).
- [ ] Check the new Peak to full scale preset on the CDJ for clipping at -0.3 dBTP ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Listen to previews at 256 and 128 frame buffers after the audio fixes and decide the default buffer size ([audio.md](dev_docs/audio.md)).
- [ ] If 128 frames still crackles, paint the zoomed waveform from a cached image so the GUI holds the GIL for less time ([audio.md](dev_docs/audio.md)).
- [ ] Try the things only simulated so far by hand: the Explorer (permission prompts, slow or unplugged drives), pinned tab dragging, drag-to-change spin boxes, trackpad pinch in the label preview and mouse drag and drop ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Look at the app on a real display in all four looks, including the splash and the label window in System 6 ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Check the rekordbox bits no sample covered: rekordbox 6 libraries, cue points and the "mid" and "low" mood phrase names ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).

### Build and release

- [ ] Add code signing and notarisation for the macOS app ([BUILDING.md](BUILDING.md)).
- [ ] Open the built `.dmg` on a clean Mac to check Gatekeeper and quarantine on the bundled ffmpeg and ffprobe ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Check the Intel build (it needs `nasm`, so only the workflow does it) and the FFmpeg tarball's GPG signature ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Check the built app bundles QtPrintSupport and that DiscRecording can reach the drive from the bundled app ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Check the update check against a real GitHub release once the repo is public, since it gets a 404 while private ([roadmap.md](dev_docs/roadmap.md)).

### Before making the repo public

- [ ] Give the README real download and install steps and re-check that every claim in it has actually been tried ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Re-read the README's trademark wording and the "not affiliated" notice ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Look through the git history for anything personal that shouldn't be public ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Write a changelog (the update check reads GitHub's release notes) ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Decide whether `AGENTS.md` and `CLAUDE.md` stay in the public repo ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read the third-party notices carefully, since they name each licence but don't reproduce the full texts ([roadmap.md](dev_docs/roadmap.md)).

### Features and app polish

- [ ] Add a drive picker (it uses the first drive) and a way to cancel to the burn window ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read a finished burn back and check it against the disc it was burned from ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Let the window split a long list across MP3 discs, move an MP3 folder to another place on its disc, and pick the disc kind from the + button ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Add a way to print labels straight after a burn, and the album or year on a label if wanted ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Give each app library its own Library tab once a second library exists ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Remember what the Explorer read across sessions (keyed by path, size and modified time) so each folder isn't probed again on its first visit ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Recognise the same song when it is a copy, or when the rekordbox library points at another drive's path ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Cache computed waveforms in SQLite so folder tracks keep their Preview strip across sessions ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Make imports and loudness measuring cancellable and show per-file progress ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Try `tinytag` as a faster folder scan, since `ffprobe` per file manages about 120 files a second ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Port AKAISDS's `wheel_guard` so scrolling over a spin box can't change it ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Finish the logging follow-ups: a Settings switch for verbose logging, logging in the store and the CLI, and a dialog for an uncaught exception ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Add rekordbox XML export as an import path with no SQLCipher dependency ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read rekordbox's `PWV3` and `PWV5` directly for exact waveform matches on rekordbox tracks ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).
- [ ] Verify the CDJ card format notes (chunk layout and checksum) against a real card file ([cdj1000mk3-card.md](dev_docs/cdj1000mk3-card.md)).
- [ ] Delete `spikes/burn_spike.py` once nothing needs it, since the window and CLI now do what it does ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Refresh dev notes that have gone stale, such as `command-line.md` saying burning is CLI-only ([command-line.md](dev_docs/command-line.md)).

### Linux and Windows

- [ ] Detect "permission denied" on the drive and say how to get access (the `cdrom` or `optical` group, or a udev rule) ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Decide between `cdrdao` and `xorriso -as cdrecord` for writing MP3 discs by trying both on a real drive ([linux-test-plan.md](dev_docs/linux-test-plan.md)).
- [ ] Tell Linux users in the guide that the rekordbox importer has no default path there (folders only, or pick a copied `master.db`) ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Check the Explorer's real drive lists on Linux and Windows, which are only tested with fake folders ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Find a trustworthy `cdrdao.exe` (build it with MSYS2 or Cygwin in CI) and find out whether it needs administrator rights ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Use IMAPI2 for MP3 discs on Windows, which needs no extra program but a COM dependency (`comtypes` or `pywin32`) and a notices entry ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Add a build job per OS to `build.yml` with the smoke test, and update `BUILDING.md`, `README.md`, `CONTRIBUTING.md` and the guide for each ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Check what `pyrekordbox`'s SQLCipher needs on Windows and Intel Linux by letting CI run its first full pass ([cross-platform.md](dev_docs/cross-platform.md)).
