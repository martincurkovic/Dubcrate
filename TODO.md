# TODO List

- [ ] Auto download update (maybe once code signing in place...)
- [x] Linux burn support
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
- [x] SD card waveform, hotcue, memory cue, loop point reverse engineering
  - [x] The waveform's 0 to 15 amplitude scale is still unknown: burn and play the test disc (made, in ~/cdj-card/wave-test-disc; dev_docs/wave-test-disc.md), then run its `fit`
- [x] SD card sync metadata tab
  - [ ] Try the Sync tab itself on a real card (the command line is proven on hardware; the tab shares its write)
  - [x] Waveform pre-writing (needs the amplitude scale)
    - [ ] Try Sync's "Write waveforms" on a Dubcrate-burned disc and the real card (the CDJ should load every track with its waveform drawn, none analysed)
- [ ] Try `dubcrate card format` (or Sync > Format a Card…, built) on a spare 2 GB card (dev_docs/card-sync.md, "Formatting a card": does the player accept it, and cues synced straight onto it?)
- [ ] Clean up long text labels in settings and add cm/in units (maybe in print dialog too)
  - [x] Check on real paper (and say if the vertical line spacing should shrink too)
  - [x] Make print dialog close when print (and resulting system print dialog is pressed)

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
- [x] Listen to previews at 256 and 128 frame buffers after the audio fixes and decide the default buffer size ([audio.md](dev_docs/audio.md)).
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

- [ ] Give the README real download and install steps and re-check that every claim in it has actually been tried ([roadmap.md](dev_docs/roadmap.md)). (the audit and suggested wording: dev_docs/release-readiness.md)
- [ ] Re-read the README's trademark wording and the "not affiliated" notice ([roadmap.md](dev_docs/roadmap.md)).
- [x] Look through the git history for anything personal that shouldn't be public ([roadmap.md](dev_docs/roadmap.md)). (scanned 2026-11-15, nothing sensitive; the decisions it leaves are in dev_docs/release-readiness.md)
- [ ] Write a changelog (the update check reads GitHub's release notes) ([roadmap.md](dev_docs/roadmap.md)). (a draft is in CHANGELOG.md: edit it)
- [x] Decide whether `AGENTS.md` and `CLAUDE.md` stay in the public repo ([roadmap.md](dev_docs/roadmap.md)). (they stay: decided 2026-11-15)
- [ ] Read the third-party notices carefully, since they name each licence but don't reproduce the full texts ([roadmap.md](dev_docs/roadmap.md)).

### Features and app polish

- [ ] Add a drive picker (it uses the first drive) and a way to cancel to the burn window ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read a finished burn back and check it against the disc it was burned from ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Let the window split a long list across MP3 discs, move an MP3 folder to another place on its disc, and pick the disc kind from the + button ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Add a way to print labels straight after a burn, and the album or year on a label if wanted ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Give each app library its own Library tab once a second library exists ([roadmap.md](dev_docs/roadmap.md)).
- [x] Remember what the Explorer read across sessions (keyed by path, size and modified time) so each folder isn't probed again on its first visit ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Recognise the same song when it is a copy, or when the rekordbox library points at another drive's path ([roadmap.md](dev_docs/roadmap.md)).
- [x] Cache computed waveforms in SQLite so folder tracks keep their Preview strip across sessions ([roadmap.md](dev_docs/roadmap.md)).
- [x] Make imports and loudness measuring cancellable and show per-file progress ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Try `tinytag` as a faster folder scan, since `ffprobe` per file manages about 120 files a second ([roadmap.md](dev_docs/roadmap.md)).
- [x] Port AKAISDS's `wheel_guard` so scrolling over a spin box can't change it ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Finish the logging follow-ups: a Settings switch for verbose logging, logging in the store and the CLI, and a dialog for an uncaught exception ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Add rekordbox XML export as an import path with no SQLCipher dependency ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read rekordbox's `PWV3` and `PWV5` directly for exact waveform matches on rekordbox tracks ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).
- [x] Verify the CDJ card format notes (chunk layout and checksum) against a real card file ([cdj1000mk3-card.md](dev_docs/cdj1000mk3-card.md)).
- [x] Delete `spikes/burn_spike.py` once nothing needs it, since the window and CLI now do what it does ([roadmap.md](dev_docs/roadmap.md)).
- [x] Refresh dev notes that have gone stale, such as `command-line.md` saying burning is CLI-only ([command-line.md](dev_docs/command-line.md)).

### Player (rbxport-style)

- [x] Phase 2 of the player: **CUE** (a temporary cue point), the **auto and manual loops** (AU / MA, IN, OUT, RELOOP, the loop size) and **Q** (quantise) work in the preview engine, session-only, never saved to rekordbox (`deck.py`, `audio/player.py`, `ui/preview.py`).
- [ ] Listen to the loops on a real track: the wrap has no crossfade (a hard cut at the loop's end), so a loop not on a zero crossing may click; add a few milliseconds of fade if it does (`audio/player.py: _take`).
- [ ] Try the new player by hand: dragging a track onto it, the hot cue and memory buttons on a real rekordbox track, the zoom buttons, and how it looks on a small screen.
- [x] Read cover art from Ogg / Opus (base64 `METADATA_BLOCK_PICTURE`) and the `ID3` chunk of AIFF and WAV files, which the Info tab does not show yet (`artwork.py`).

### Cues: Dubcrate, rekordbox and the card

- [x] Edit memory cues in the player (kept in Dubcrate's own library, layered over rekordbox's; `library/cue_edits.py`).
- [x] Writing memory cues to rekordbox itself (stage 3 of `dev_docs/rekordbox-cue-writes.md`): `dubcrate rekordbox write-memory-cues` is built, tested on synthetic databases and dry-run against a snapshot of the real library.
- [x] Live test of `dubcrate rekordbox write-memory-cues --yes` (2026-11-15: the owner ran it on their library, "worked perfectly").
- [x] Memory cue edits in the app are saved into rekordbox as they are made, after a one-time question and with a Settings > Library checkbox (`ui/rekordbox_editor.py`).
- [ ] Try that in-app path by hand with rekordbox closed (edit a cue in the player, accept the question, open rekordbox), and with it open (the edit should wait, with a status message).
- [x] Import memory cues set on the CDJ back from the card (additions only: Sync tab > Import Cues…, `card_import.py`).
- [x] Import Cues… also brings back loops and the player's hot cues (unticked), and notices cues deleted or moved on the player, against a baseline each sync records (`card_import.py`, schema v15; 2026-11-16, tested offscreen only).
- [ ] Try Import Cues… with cues really set, deleted and moved on the player (sync a disc, change it on the player, import): what does the player do to the card's file when a cue is deleted or moved (`dev_docs/card-sync.md`, "Importing changes from the card")?
- [x] Right-click a card in the Sync tab's list: **Import Cues from “NAME”…** beside Sync and Eject (the button in the discs panel is the only way now).
- [x] Write the edits that are waiting in Dubcrate (made while rekordbox was open, or before editing was allowed) in one go from the app: File > Save Waiting Memory Cue Edits to rekordbox (not tried live).
- [x] Save memory loops in Dubcrate: MEMORY with a loop on (`memory_loop` table; shown in the player, on the card as memory loops, counted in the ten; not written to rekordbox).
- [ ] Try memory loops by hand: set a loop, press MEMORY, check the Memory tab row and ✕, sync to the card and see the loop on the player.
- [x] Make three memory loops in rekordbox and read their rows (2026-11-16: DANGER DANGER (Extended); `dev_docs/rekordbox-cue-writes.md`, "Memory loops").
- [x] Write memory loops into rekordbox (`rekordbox_write.py`, add-only, tested on synthetic databases): `dubcrate rekordbox write-memory-cues --loops`. In the app too (`RekordboxEditor.WRITE_LOOPS`, on since 2026-11-16).
- [x] Tried `dubcrate rekordbox write-memory-cues --loops` on a copy and then on the real library (2026-11-16: the loop on "SOS" was in rekordbox and behaved), and switched `RekordboxEditor.WRITE_LOOPS` on.
- [ ] Try the in-app loop path by hand with rekordbox closed (save a loop with MEMORY on a rekordbox track, open rekordbox: it is in the memory list), and with it open (the loop should wait, with a status message).
- [ ] Hot cues and loops: edit hot cues in the player and write them to rekordbox (`dev_docs/rekordbox-cue-writes.md` has the `Kind` numbers; only memory cues are written so far).

### Linux and Windows

- [ ] Run the Linux AppImage on a real desktop (Ubuntu or Fedora): open it, play a preview, burn a test disc, eject a card (`dev_docs/linux-test-plan.md`); then try it on an older glibc, since an AppImage built on Ubuntu 24.04 needs 2.39 or newer ([BUILDING.md](BUILDING.md)).
- [ ] Build for Windows: a build script, a `build.yml` job and an installer (`jv-studio`'s `build_windows.ps1` and Inno Setup are the model), and an LGPL ffmpeg for it ([cross-platform.md](dev_docs/cross-platform.md), Phase 5).
- [ ] Try eject (card and disc) and the disc's TOC reader on a real Linux and a real Windows machine (`eject.py`, `drive_toc.py`; tested against stand-in commands only) ([cross-platform.md](dev_docs/cross-platform.md)).
- [x] Use `pycdlib` to make the MP3 disc's ISO on every OS, so no `xorriso` is needed and Windows can make MP3 discs ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 4).
- [ ] Burn an MP3 disc from a `pycdlib` image (`dubcrate export`'s `disc.iso`, in Brasero or Finder, or `cdrdao` on Linux) and play it in a CDJ and a car player; try a disc 8 folders deep ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Guard Windows' reserved names (`CON`, `NUL`, `COM1`...) in MP3 folder names, and try a long path on Windows ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 5).
- [ ] Notice an SD card being plugged in on Windows, which has no folder to watch ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 6).
- [ ] Try Format a Card on a spare 2 GB card on Linux and on Windows (built, tested against stand-in commands only: `card_format_linux.py`, `card_format_windows.py`; the Linux layout was checked with the real `sfdisk` and `mkfs.fat` on an image file).
- [ ] Detect "permission denied" on the drive and say how to get access (the `cdrom` or `optical` group, or a udev rule) ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Decide between `cdrdao` and `xorriso -as cdrecord` for writing MP3 discs by trying both on a real drive ([linux-test-plan.md](dev_docs/linux-test-plan.md)).
- [x] Tell Linux users in the guide that the rekordbox importer has no default path there (folders only, or pick a copied `master.db`) ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Check the Explorer's real drive lists on Linux and Windows, which are only tested with fake folders ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Find a trustworthy `cdrdao.exe` (build it with MSYS2 or Cygwin in CI) and find out whether it needs administrator rights ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Use IMAPI2 for MP3 discs on Windows, which needs no extra program but a COM dependency (`comtypes` or `pywin32`) and a notices entry ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Add a build job per OS to `build.yml` with the smoke test, and update `BUILDING.md`, `README.md`, `CONTRIBUTING.md` and the guide for each ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Check what `pyrekordbox`'s SQLCipher needs on Windows and Intel Linux by letting CI run its first full pass ([cross-platform.md](dev_docs/cross-platform.md)).
