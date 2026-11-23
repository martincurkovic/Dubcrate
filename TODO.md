# TODO List

Open items by area; finished work is under **Completed** at the bottom (same headings).

## General

- [ ] Auto download update (maybe once code signing in place...)
- [ ] Try on a real Linux machine + drive (dev_docs/linux-test-plan.md) (follow-up to: Linux burn support) (2026-11-23: done on Omarchy/Arch, the audio disc played on a CDJ with CD-TEXT; **only the MP3 disc's result is left**, its burn was started: see Completed)
- [ ] Windows burn support
  - [ ] Spike on a real Windows machine (cdrdao + CD-TEXT)
- [ ] Linux build script (AppImage / Flatpak / uv tool?) (the owner wants the dependencies bundled for end users, 2026-11-23: ffmpeg is, `cdrdao` is not; decide whether to bundle it, it is GPL-2.0-or-later)
- [ ] Windows build script and installer: written 2026-11-17 (`scripts/build_windows.ps1`, `build_ffmpeg_windows.sh`, `assets/windows/Dubcrate.iss`, a `build-windows` job in `build.yml`), **never run**: see "Linux and Windows" below
- [ ] Mixxx library support
- [ ] Engine DJ library support
- [ ] Serato library support
- [ ] Linux / Windows (they use the system ffmpeg for now) (follow-up to: Bundle dependencies)
- [ ] Drop `experimental` from the Windows leg in test.yml once happy (follow-up to: CI test legs for Linux, macOS, Windows)
- [ ] Homebrew packaging once first release public
- [ ] Maybe use some real songs, not fake ones (follow-up to: Screenshot staging)
- [ ] Clean up user facing docs (readme, user guide)

### Hardware checks (Mac and the CDJ-1000MK3)

- [ ] Find out what unit `DRPreGapLengthKey` uses before trusting the 1, 2 and 3 s "Between tracks" options ([burning.md](dev_docs/burning.md)).
- [ ] Compare the drive's reported free blocks on a 74 and an 80 minute disc (expect about 333,000 and 359,850) to confirm the capacity check ([burning.md](dev_docs/burning.md)).
- [ ] Burn a small MP3 disc and check on the CDJ its load time, file order, what it shows (ID3 or file name) and whether ffmpeg's Info frame bothers it (`-write_xing 0` is the fallback) ([burning.md](dev_docs/burning.md)).
- [ ] Decode the MP3 disc's card files (`PIONEER/MPJ`): two looks 2026-11-23, see `dev_docs/cdj1000mk3-card.md` section 6 (one file for the whole disc, waveform records keyed by (folder, file), the audio checksum validates them, the ID is built from the ISO: its size and the last file's start). Cue entries are decoded (132 bytes: position in 1/75 s, MP3 frame number and byte offset, track length; the player seeks by byte offset) from a root memory cue, hot cue A and loop. Next: cues inside a folder (how the folder enters), hot cue B, cue timing against audio time, then a second image for the rest of the ID; then MP3 waveforms and cues in Sync (the CDJ builds an MP3's waveform at real-time speed, so pre-writing them would help most there).
- [ ] Try MP3 disc folders on the CDJ, starting with two nested folders, then the 999-file and 8-level limits and whether `MAX_DEPTH` should be 9 ([burning.md](dev_docs/burning.md)).
- [ ] Burn a nearly full MP3 disc to check the size estimate and `FILESYSTEM_OVERHEAD_SECTORS` ([burning.md](dev_docs/burning.md)).
- [ ] Check the rekordbox bits no sample covered: rekordbox 6 libraries, cue points and the "mid" and "low" mood phrase names ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).

### Build and release

- [ ] Add code signing and notarisation for the macOS app ([BUILDING.md](BUILDING.md)).
- [ ] Check the update check against a real GitHub release once the first one is published (the repo is public, so until then the check just says you're up to date) ([roadmap.md](dev_docs/roadmap.md)).

### Before the first release

- [ ] Write a changelog (the update check reads GitHub's release notes) ([roadmap.md](dev_docs/roadmap.md)). (a draft is in CHANGELOG.md: edit it)

### Features and app polish

- [ ] Finder page: try **Tagged** in the iPhone viewer app (do the tags survive closing the app? the line under the buttons says "kept on this device" or "NOT kept"), and drag a tagged track up and down with a finger, and try Filter (BPM, year, key, genre), the waveform and memory cue view (tap a card) and Clear on the phone, with the owner's real library (the first export analyses waveforms: how long does it take?); then add the played tick, by-disc grouping and share / copy list.
- [ ] Try the drive picker with two real drives (the check after a burn is skipped for any but the first).
- [ ] Read a rekordbox USB stick's own database (`.PIONEER/rekordbox/export.pdb` and `USBANLZ`) so a stick can be imported with BPM, key, cues and beat grids: today only a computer's `master.db` or a folder of audio can be (the rekordbox stick the owner brought to Linux imported as a folder, tags only).
- [ ] Find out why one simulated burn on Linux failed with `Cannot set write parameters mode page` (2026-11-23, on a disc just loaded; five later runs, including `generic-mmc-raw`, were fine): [burning.md](dev_docs/burning.md), "Linux and `cdrdao`".
- [ ] Ask the drive for its top write speed: the burn window offered 48x and `cdrdao` quietly wrote at 24x (the log says so, the window does not).
- [ ] Try the disc and card auto-detection on macOS and Windows (`card_format.card_devices`, `drive_toc.disc_state`; Linux works, macOS only has `/dev` and `drutil status` guesses, Windows can't say and still needs Refresh / Check Again for a blank card or a disc).
- [ ] Make it clear when a Sync has finished (it is a status-bar message that the list refresh replaces within seconds): a banner in the Sync tab that stays until the next action was suggested, not decided.
- [ ] Cancel the writing of a burn itself (not built: the backends have no abort; DiscRecording's `DRBurn.abort()` exists and `cdrdao` can be terminated, but a stopped write ruins the disc, so it needs a confirmation and a try on a drive).
- [ ] Try dragging an MP3 folder in the Discs list by hand (the feel, and dropping on a collapsed disc).
- [ ] Give each app library its own Library tab once a second library exists ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Try `tinytag` as a faster folder scan, since `ffprobe` per file manages about 120 files a second ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Add rekordbox XML export as an import path with no SQLCipher dependency ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read rekordbox's `PWV3` and `PWV5` directly for exact waveform matches on rekordbox tracks ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).

### Linux and Windows

- [ ] Run the Linux AppImage on a real desktop (Ubuntu or Fedora): open it, play a preview, burn a test disc, eject a card (`dev_docs/linux-test-plan.md`); then try it on an older glibc, since an AppImage built on Ubuntu 24.04 needs 2.39 or newer ([BUILDING.md](BUILDING.md)).
- [ ] Run the Windows build again (`build.yml` by hand) and fix what it shows. The first run (2026-11-17) got through the MSYS2 ffmpeg build (about 14 minutes) and Nuitka (about 28), then stopped because Nuitka's `--include-data-dir` leaves out `.exe` files (fixed: the two tools are named one by one). Not yet seen: the smoke test (`sqlcipher3`, `miniaudio`), the installer, the icon, `--windows-console-mode=disable` ([cross-platform.md](dev_docs/cross-platform.md), Phase 5).
- [ ] Install the built installer on a clean Windows 10 or 11 machine or VM: it installs without an administrator prompt, the app opens and plays a preview, Settings > Library shows the bundled ffmpeg, Export Disc as Files works, the uninstaller removes it, and an upgrade over the old one keeps its settings and library.
- [ ] Run `test-windows.yml` by hand again: the 2026-11-17 run had 36 failures, all two causes now fixed (the rekordbox database stayed open after `close()`, so Windows couldn't delete its temp folder: `rekordbox.close_database`; `os.O_NONBLOCK` doesn't exist on Windows), so check it comes back clean.
- [ ] Try eject (card and disc) and the disc's TOC reader on a real Linux and a real Windows machine (`eject.py`, `drive_toc.py`; tested against stand-in commands only) ([cross-platform.md](dev_docs/cross-platform.md)). (Linux 2026-11-23: the TOC reader, card eject and disc eject all work; **Windows remains**.)
- [ ] Burn an MP3 disc from a `pycdlib` image (`dubcrate export`'s `disc.iso`, in Brasero or Finder, or `cdrdao` on Linux) and play it in a CDJ and a car player; try a disc 8 folders deep ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Try a long path on Windows (the reserved names are guarded in folder names and in the disc's staging folder, 2026-11-16; the 259-character limit is refused up front, `build.path_too_long`: whether a longer path would really fail there is unchecked) ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 5).
- [ ] Try Format a Card on a spare 2 GB card on Linux and on Windows (built, tested against stand-in commands only: `card_format_linux.py`, `card_format_windows.py`; the Linux layout was checked with the real `sfdisk` and `mkfs.fat` on an image file). (Linux 2026-11-23: it formats a real card correctly after the fixes (one format left no file system, cause unknown, not seen again); **Windows remains**.)
- [ ] Try the Linux drive permission message on a real machine (take yourself out of the drive's group, then add yourself back without logging out): `cdrdao.access_problem` names the group and the `usermod` line, or says to log out and in; tested with stand-ins only ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Decide between `cdrdao` and `xorriso -as cdrecord` for writing MP3 discs by trying both on a real drive ([linux-test-plan.md](dev_docs/linux-test-plan.md)).
- [ ] Check the Explorer's real drive lists on Linux and Windows, which are only tested with fake folders ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Find a trustworthy `cdrdao.exe` (build it with MSYS2 or Cygwin in CI) and find out whether it needs administrator rights ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Use IMAPI2 for MP3 discs on Windows, which needs no extra program but a COM dependency (`comtypes` or `pywin32`) and a notices entry ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Update `README.md`, `CONTRIBUTING.md` and the guide per OS now that every OS has a build job (`BUILDING.md` is done; the guide and CONTRIBUTING still talk about a Mac in places) ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Check what `pyrekordbox`'s SQLCipher needs on Windows and Intel Linux by letting CI run its first full pass ([cross-platform.md](dev_docs/cross-platform.md)).

## Completed

### General

- [x] Linux burn support
- [x] Audio normalisation setting?
- [x] Bundle dependencies (ffmpeg, et. al)
- [x] CI test legs for Linux, macOS, Windows
- [x] Screenshot staging
- [x] SD card waveform, hotcue, memory cue, loop point reverse engineering
  - [x] The waveform's 0 to 15 amplitude scale is still unknown: burn and play the test disc (made, in ~/cdj-card/wave-test-disc; dev_docs/wave-test-disc.md), then run its `fit`
- [x] SD card sync metadata tab
  - [x] Waveform pre-writing (needs the amplitude scale)
    - [x] Analyse a track's waveform when it is put on an audio disc (2026-11-16: in the background, so the Sync tab's Analyse Waveforms button is gone; a burn and a sync still fill in what is left)
- [x] Clean up long text labels in settings and add cm/in units (maybe in print dialog too) (2026-11-16: Settings > Appearance > Units, used by the label window and its paper list; the long Settings paragraphs are shorter. The checkbox titles were kept as the guide and tests quote them.)
  - [x] Check on real paper (and say if the vertical line spacing should shrink too)
  - [x] Make print dialog close when print (and resulting system print dialog is pressed)
- [x] Review every window, dialog, message and menu in the UI gallery for layout and wording (`uv run python scripts/ui_snapshots.py`, then open `build/ui_snapshots/index.html`: mark each screen, write notes, Export notes). Each screen lists the layout flags the checker found.
- [x] Try on a real disc + CDJ (waveform height, any clipping, is -10 LUFS right?) (follow-up to: Audio normalisation setting)
- [x] Run build.yml in CI (bundled ffmpeg, smoke test, does PySide6 import on macOS 15?)
- [x] Hardware checks on the Mac: burn window, MP3 disc
- [x] Try the Sync tab itself on a real card (the command line is proven on hardware; the tab shares its write) (follow-up to: SD card sync metadata tab)
- [x] Try Sync's waveforms (always written now: the "Write waveforms" box is gone) on a Dubcrate-burned disc and the real card (the CDJ should load every track with its waveform drawn, none analysed) (follow-up to: SD card sync metadata tab)
- [x] Try `dubcrate card format` (or Sync > Format a Card…, built) on a spare 2 GB card (dev_docs/card-sync.md, "Formatting a card": does the player accept it, and cues synced straight onto it?)

### Hardware checks (Mac and the CDJ-1000MK3)

- [x] Paint the zoomed waveform without long GIL holds (2026-11-16: the 3-band look is a numpy image now, 3.4 ms a frame; no late audio callbacks at 128 or 256 frames offscreen) ([audio.md](dev_docs/audio.md)).
- [x] Listen to previews at 256 and 128 frame buffers after the audio fixes and decide the default buffer size ([audio.md](dev_docs/audio.md)).
- [x] Investigate 60 FPS waveform scrolling (2026-11-16, `spikes/waveform_fps.py`, everything in [waveform-60fps.md](dev_docs/waveform-60fps.md), short version in [audio.md](dev_docs/audio.md)): possible, worth doing; the 3-band look takes 16 ms a frame (the whole 60 FPS budget) because of three polygon fills, so it needs a cheaper renderer first (a numpy image: 2 to 5 ms).
- [x] Build 60 FPS scrolling (2026-11-16): an image-based 3-band look (16 ms -> 3.4 ms a frame, checked against the polygons in dark, light and System 6), the position moving between audio blocks, a 16 ms tick, and Settings > Appearance > Waveform speed (Smooth 60 / Light 30). No late audio callbacks at 128 or 256 frames offscreen ([waveform-60fps.md](dev_docs/waveform-60fps.md), "What was built").
- [x] Confirm a burned disc is really gapless (tracks at 00:02.00, 00:42.00, 01:12.00 in `drutil toc`) now that the 2 s pregap fix is in ([burning.md](dev_docs/burning.md)).
- [x] Check the new Peak to full scale preset on the CDJ for clipping at -0.3 dBTP ([roadmap.md](dev_docs/roadmap.md)).
- [x] Try the 60 FPS waveform by eye and ear on the Mac (smooth scroll, in step with the sound, no tearing; the Light setting; a 120 Hz display). If it judders, follow `QScreen.refreshRate()` or take the position at paint time.
- [x] Try the things only simulated so far by hand: the Explorer (permission prompts, slow or unplugged drives), pinned tab dragging, drag-to-change spin boxes, trackpad pinch in the label preview and mouse drag and drop ([roadmap.md](dev_docs/roadmap.md)).
- [x] Look at the app on a real display in all four looks, including the splash and the label window in System 6 ([roadmap.md](dev_docs/roadmap.md)).

### Build and release

- [x] Open the built `.dmg` on a clean Mac to check Gatekeeper and quarantine on the bundled ffmpeg and ffprobe ([roadmap.md](dev_docs/roadmap.md)).
- [x] Check the Intel build (it needs `nasm`, so only the workflow does it) and the FFmpeg tarball's GPG signature ([roadmap.md](dev_docs/roadmap.md)).
- [x] Check the built app bundles QtPrintSupport and that DiscRecording can reach the drive from the bundled app ([roadmap.md](dev_docs/roadmap.md)).

### Before the first release

- [x] Look through the git history for anything personal that shouldn't be public ([roadmap.md](dev_docs/roadmap.md)). (scanned 2026-11-15, nothing sensitive; the decisions it leaves are in dev_docs/release-readiness.md)
- [x] Decide whether `AGENTS.md` and `CLAUDE.md` stay in the public repo ([roadmap.md](dev_docs/roadmap.md)). (they stay: decided 2026-11-15)
- [x] Give the README real download and install steps and re-check that every claim in it has actually been tried ([roadmap.md](dev_docs/roadmap.md)). (the audit and suggested wording: dev_docs/release-readiness.md)
- [x] Re-read the README's trademark wording and the "not affiliated" notice ([roadmap.md](dev_docs/roadmap.md)).
- [x] Read the third-party notices carefully, since they name each licence but don't reproduce the full texts ([roadmap.md](dev_docs/roadmap.md)).

### Features and app polish

- [x] Let Export Disc as Files… be cancelled (2026-11-17): the Cancel button stops it between tracks and removes the half-made folder.
- [x] Year on labels (2026-11-16): **Show year** in the label window, off by default, between key and time; from rekordbox or the file's tags. (The album was asked for and skipped: not wanted.)
- [x] Guard Windows' reserved names in MP3 folder names and the disc's staging folder (`mp3.dir_name`, `mp3.volume_name`).
- [x] Detect "permission denied" on the drive on Linux and say how to get access, naming the drive's group (`cdrdao.access_problem`, 2026-11-16).
- [x] Cancel a burn while the disc is being prepared (2026-11-16: a Cancel button in the burn window that stops the rendering at once and removes what it made; it goes once writing starts). Tested with fakes and real ffmpeg.
- [x] Docs cleanup (2026-11-16): README, changelog draft, guide, CONTRIBUTING and `dev_docs/` brought up to date (README claims fixed, CLI doc completed, stale status paragraphs and the roadmap's duplicated checklist corrected, a grouped `dev_docs/README.md`).
- [x] A UI review gallery like Cobalt's (2026-11-16: `scripts/ui_snapshots.py`): every window, dialog, message box and menu rendered in all three looks, with layout checks, a searchable inventory of every text and tooltip, per-screen status and notes.
- [x] Sync tab tidy-up (2026-11-16): no help text under an empty card list, no Status / Cues / Waveform columns or Details button, waveforms always written (no tick box), the disc list's tick boxes on the left of each name and a select-all box in its heading.
- [x] A badge in the status bar's bottom right corner: yellow "Library read-only" or orange "Library writable" (only with a rekordbox library imported); the status bar is a shade of its own with a rule on top (`ui/status_bar.py`).
- [x] Add a drive picker to the burn window (2026-11-16: a menu shown only with two or more drives, remembered by name; `Burner.drives()`; tested with fakes). ([roadmap.md](dev_docs/roadmap.md))
- [x] MP3 disc editor (2026-11-16): split the shown tracks across MP3 discs (Disc menu), move an MP3 folder (drag in the Discs list, or right-click > Move Folder to), and the + asks for an audio or MP3 disc ([roadmap.md](dev_docs/roadmap.md)).
- [x] Add a way to print labels straight after a burn (2026-11-16: a **Print Labels…** button in the burn window once a real burn has finished; it closes the window and opens the labels with the disc ticked). ([roadmap.md](dev_docs/roadmap.md))
- [x] Remember what the Explorer read across sessions (keyed by path, size and modified time) so each folder isn't probed again on its first visit ([roadmap.md](dev_docs/roadmap.md)).
- [x] Recognise the same song when it is a copy, or when the rekordbox library points at another drive's path ([roadmap.md](dev_docs/roadmap.md)). (Decided 2026-11-16: **match on path only**, as it already does through `store.path_key`; never on name or content, so a copy or another drive's path is a different file. Nothing more to build.)
- [x] Cache computed waveforms in SQLite so folder tracks keep their Preview strip across sessions ([roadmap.md](dev_docs/roadmap.md)).
- [x] Make imports and loudness measuring cancellable and show per-file progress ([roadmap.md](dev_docs/roadmap.md)).
- [x] Port AKAISDS's `wheel_guard` so scrolling over a spin box can't change it ([roadmap.md](dev_docs/roadmap.md)).
- [x] Finish the logging follow-ups: a Settings switch for verbose logging, logging in the store and the CLI, and a dialog for an uncaught exception ([roadmap.md](dev_docs/roadmap.md)). (2026-11-16: the switch was done earlier; the store logs its imports, upgrades and deletions; the command line logs to `dubcrate-cli.log`; an unexpected error shows a "Something went wrong" message with a Show Log File button, `ui/crash_notice.py`.)
- [x] Verify the CDJ card format notes (chunk layout and checksum) against a real card file ([cdj1000mk3-card.md](dev_docs/cdj1000mk3-card.md)).
- [x] Delete `spikes/burn_spike.py` once nothing needs it, since the window and CLI now do what it does ([roadmap.md](dev_docs/roadmap.md)).
- [x] Refresh dev notes that have gone stale, such as `command-line.md` saying burning is CLI-only ([command-line.md](dev_docs/command-line.md)).
- [x] Read a finished burn back and check it against the disc it was burned from ([roadmap.md](dev_docs/roadmap.md)).

### Player (rbxport-style)

- [x] Grey out Q and show manual loops for a track rekordbox hasn't analysed (no beat grid): a tag's BPM alone no longer quantises (owner's idea, 2026-11-23). Q's setting is kept for the next track with a grid; the loop mode the user chose comes back with it. The beat jump menu is in seconds for such a track (1 s to 1 min, its own setting).
- [x] Phase 2 of the player: **CUE** (a temporary cue point), the **auto and manual loops** (AU / MA, IN, OUT, RELOOP, the loop size) and **Q** (quantise) work in the preview engine, session-only, never saved to rekordbox (`deck.py`, `audio/player.py`, `ui/preview.py`).
- [x] Listen to the loops on a real track: the wrap has no crossfade (a hard cut at the loop's end), so a loop not on a zero crossing may click; add a few milliseconds of fade if it does (`audio/player.py: _take`). (2026-11-16: the owner heard them, "the audio works fine"; no fade added.)
- [x] Bar and beat counter beside the zoomed waveform's playhead (`13.3 Bars`: `barcount.py`, `DetailView._paint_bar_counter`).
- [x] Loop mark (a small orange circular arrow) after a loop's time in the Memory tab, as rbxport does; every memory loop has a ✕ (`player_side.py`).
- [x] Click a loop row in the Memory tab to jump to its start with that loop on (`PreviewController.recall_loop`, `Deck.recall_loop`).
- [x] Auto loop sizes down to 1/64 of a beat (minimum loop 2 ms), and halving a loop with the playhead in the half that goes keeps the playhead in the loop, in time (`Deck`, `PreviewController._keep_in_loop`).
- [x] Quantise (Q) is on the first time and remembered after that (`settings.get_quantise`).
- [x] Removed the "Edited here" note and the Reset button from the Memory tab (Reset is in its right-click menu) and the "Memory cues saved to rekordbox" message.
- [x] Fixed the lag after pressing MEMORY on a playing track: the refresh asked for the rekordbox twin of every visible row by rebuilding a path map of the whole library each time (215 ms to 4 ms; `Library._rekordbox_rows_by_file`). The backup was not the cause (5 ms).
- [x] Read cover art from Ogg / Opus (base64 `METADATA_BLOCK_PICTURE`) and the `ID3` chunk of AIFF and WAV files, which the Info tab does not show yet (`artwork.py`).

### Cues: Dubcrate, rekordbox and the card

- [x] Edit memory cues in the player (kept in Dubcrate's own library, layered over rekordbox's; `library/cue_edits.py`).
- [x] Writing memory cues to rekordbox itself (stage 3 of `dev_docs/rekordbox-cue-writes.md`): `dubcrate rekordbox write-memory-cues` is built, tested on synthetic databases and dry-run against a snapshot of the real library.
- [x] Live test of `dubcrate rekordbox write-memory-cues --yes` (2026-11-15: the owner ran it on their library, "worked perfectly").
- [x] Memory cue edits in the app are saved into rekordbox as they are made, after a one-time question and with a Settings > Library checkbox (`ui/rekordbox_editor.py`).
- [x] Import memory cues set on the CDJ back from the card (additions only: Sync tab > Import Cues…, `card_import.py`).
- [x] Import Cues… also brings back loops and the player's hot cues (unticked), and notices cues deleted or moved on the player, against a baseline each sync records (`card_import.py`, schema v15; 2026-11-16, tested offscreen only).
- [x] Right-click a card in the Sync tab's list: **Import Cues from “NAME”…** beside Sync and Eject (the button in the discs panel is the only way now).
- [x] Write the edits that are waiting in Dubcrate (made while rekordbox was open, or before editing was allowed) in one go from the app: File > Save Waiting Memory Cue Edits to rekordbox (not tried live).
- [x] Save memory loops in Dubcrate: MEMORY with a loop on (`memory_loop` table; shown in the player, on the card as memory loops, counted in the ten; not written to rekordbox).
- [x] Make three memory loops in rekordbox and read their rows (2026-11-16: DANGER DANGER (Extended); `dev_docs/rekordbox-cue-writes.md`, "Memory loops").
- [x] Write memory loops into rekordbox (`rekordbox_write.py`, add-only, tested on synthetic databases): `dubcrate rekordbox write-memory-cues --loops`. In the app too (`RekordboxEditor.WRITE_LOOPS`, on since 2026-11-16).
- [x] Tried `dubcrate rekordbox write-memory-cues --loops` on a copy and then on the real library (2026-11-16: the loop on "SOS" was in rekordbox and behaved), and switched `RekordboxEditor.WRITE_LOOPS` on.
- [x] Memory loops can be deleted in Dubcrate even once rekordbox has them (a removal list, `memory_loop_removed`; soft delete in rekordbox, like a memory cue). Tested on synthetic databases only.
- [x] Try that in-app path by hand with rekordbox closed (edit a cue in the player, accept the question, open rekordbox), and with it open (the edit should wait, with a status message).
- [x] Try Import Cues… with cues really set, deleted and moved on the player (sync a disc, change it on the player, import): what does the player do to the card's file when a cue is deleted or moved (`dev_docs/card-sync.md`, "Importing changes from the card")?
- [x] Try memory loops by hand: set a loop, press MEMORY, check the Memory tab row and ✕, sync to the card and see the loop on the player. (The Memory tab row, the loop mark and recalling a loop are confirmed by the owner, 2026-11-16: "loops behave exactly as expected"; syncing one to the player is not.)
- [x] Try the in-app loop path by hand with rekordbox closed (save a loop with MEMORY on a rekordbox track, open rekordbox: it is in the memory list), and with it open (the loop should wait, with a status message).
- [x] Try deleting a loop that rekordbox has, from the app (✕ on its row in the Memory tab, rekordbox closed, then open rekordbox: the loop is gone) and from the command line on a copy first (`dubcrate rekordbox write-memory-cues --loops --db COPY`; `spikes/rekordbox_loop_rows.py --db COPY` then shows no loop, since it lists live rows).
- [x] Hot cues and loops: edit hot cues in the player and write them to rekordbox (`dev_docs/rekordbox-cue-writes.md` has the `Kind` numbers; only memory cues are written so far).

### Linux and Windows

- [x] Check the volume label of a card formatted on Linux (2026-11-23): it is `DISK_IMG`; the "Card Ready" box had quoted the mount folder's name, the serial `8B78-BD46`, because udisks mounted before it had read the label, and now says the label.
- [x] A disc `cdrdao` wrote on Linux plays on a CDJ-1000MK3: CD-TEXT shows, the audio is right, gapless as far as the owner can tell; Dubcrate's disc eject after the burn works; Format a Card works on a real card (2026-11-23, owner).
- [x] First real Linux session (2026-11-23, Omarchy / Arch, Wayland, a real drive and CDJ card): tests (the 2 failures were Qt's `&` mnemonics in test strings), start-up on Wayland and xcb, audio preview, PDF labels, audio and MP3 export read back by `cdrdao` and `xorriso`, the real drive's `disk-info`, a simulated and a real audio burn at 24x with the TOC check "exactly as planned", the kernel TOC reader matching the card's entry for a Mac-burned disc, the built-in SD slot, Sync with waveforms, a loop set on the CDJ and imported back, Eject after syncing ([linux-test-plan.md](dev_docs/linux-test-plan.md), [burning.md](dev_docs/burning.md), [cross-platform.md](dev_docs/cross-platform.md)).
- [x] Fix card formatting on Linux: the false "unformatted" (udev not yet refreshed), the automounted new partition that made `wipefs` fail "busy", and the script's own check and logging (2026-11-23).
- [x] Format a Card closes itself and shows a "Card Ready" box when it finishes (2026-11-23).
- [x] The card list and the burn window notice a card or a disc by themselves (no Refresh / Check Again), and an ejected card in a built-in slot is hidden (2026-11-23).
- [x] Notice an SD card being plugged in on Windows, which has no folder to watch: `explorer.DrivePoller` polls `mounted_volumes()` every 3 s while the Explorer or the card list is on show ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 6). Tested with fake drive lists, not tried on Windows.
- [x] Write the Windows build (2026-11-17): `build_windows.ps1`, `build_ffmpeg_windows.sh` (MSYS2), `assets/windows/Dubcrate.iss`, `make_ico.py`, a `build-windows` job, tests in `test_build_files.py` and `BUILDING.md`. Written, never run (the open items above).
- [x] Use `pycdlib` to make the MP3 disc's ISO on every OS, so no `xorriso` is needed and Windows can make MP3 discs ([cross-platform.md](dev_docs/cross-platform.md), Phase 6 step 4).
- [x] Tell Linux users in the guide that the rekordbox importer has no default path there (folders only, or pick a copied `master.db`) ([cross-platform.md](dev_docs/cross-platform.md)).
