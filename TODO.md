# TODO List

Open items by area, each one still to do or to try; everything built is under **Completed** at the bottom (same headings). When something is done, move it there.

## General

- [ ] Auto download update (maybe once code signing in place...)
- [ ] Run the CI AppImage build (`build.yml`) once: it now bundles `cdrdao` (the AppImage built here runs on the owner's desktop)
- [ ] Run the Windows build in CI (`build.yml` by hand) once: the VM built and installed it (2026-11-24), CI needs the MSYS shell packages for `cdrdao`
- [ ] Mixxx library support
- [ ] Engine DJ library support
- [ ] Serato library support
- [ ] Drop `experimental` from the Windows leg in test.yml once happy (follow-up to: CI test legs for Linux, macOS, Windows)
- [ ] Homebrew packaging once first release public
- [ ] Maybe use some real songs, not fake ones (follow-up to: Screenshot staging)

### Hardware checks (Mac and the CDJ-1000MK3)

- [ ] Find out what unit `DRPreGapLengthKey` uses before trusting the 1, 2 and 3 s "Between tracks" options ([burning.md](dev_docs/burning.md)).
- [ ] Compare the drive's reported free blocks on a 74 and an 80 minute disc (expect about 333,000 and 359,850) to confirm the capacity check ([burning.md](dev_docs/burning.md)).
- [ ] MP3 cue timing: a click-track disc to measure the player's time against ours, maybe rounding cues to the nearest MP3 frame, and the four bytes at +112 of a cue entry ([card-sync.md](dev_docs/card-sync.md), [cdj1000mk3-card.md](dev_docs/cdj1000mk3-card.md) section 6).
- [ ] MP3 discs: Mac/DiscRecording burns aren't recorded, the Disc ID window can't read a disc on macOS and Windows, and discs with another label are refused ([card-sync.md](dev_docs/card-sync.md)).
- [ ] Try Import Cues… on an MP3 disc with cues set, deleted and moved on a real player: sync, change the cues on the player, import (what the player does to a deleted or moved MP3 cue is assumed to match an audio disc; a disc synced before 2026-11-26 has no baseline until its next sync) ([card-sync.md](dev_docs/card-sync.md), "Importing from an MP3 disc").
- [ ] MP3 sync: nested folders and non-MPEG-1 Layer III files are refused (how the player numbers a nested folder isn't known: the nested-folder item below tests it).
- [ ] Try MP3 disc folders on the CDJ, starting with two nested folders, then the 999-file and 8-level limits and whether `MAX_DEPTH` should be 9 ([burning.md](dev_docs/burning.md)).
- [ ] Check the rekordbox bits no sample covered: rekordbox 6 libraries, cue points and the "mid" and "low" mood phrase names ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).

### Build and release

- [ ] Add code signing and notarisation for the macOS app ([BUILDING.md](BUILDING.md)).
- [ ] Check the update check against a real GitHub release once the first one is published (the repo is public, so until then the check just says you're up to date) ([roadmap.md](dev_docs/roadmap.md)).

### Before the first release

- [ ] Write a changelog (the update check reads GitHub's release notes) ([roadmap.md](dev_docs/roadmap.md)). (A short plain draft is in CHANGELOG.md: read it once more before tagging.)

### Features and app polish

- [ ] Try the drive picker with two real drives (the check after a burn is skipped for any but the first).
- [ ] Burn through the USB-C hub once, to see the `generic-mmc` retry write a disc (the default driver was refused behind the hub, 2026-11-24) ([burning.md](dev_docs/burning.md)).
- [ ] Make it clear when a Sync has finished (it is a status-bar message that the list refresh replaces within seconds): a banner in the Sync tab that stays until the next action was suggested, not decided.
- [ ] Cancel the writing of a burn itself (needs a confirmation: a stopped write ruins the disc).
- [ ] Try dragging an MP3 folder in the Discs list by hand (the feel, and dropping on a collapsed disc).
- [ ] Give each app library its own Library tab once a second library exists ([roadmap.md](dev_docs/roadmap.md)).
- [ ] Read rekordbox's `PWV3` and `PWV5` directly for exact waveform matches on rekordbox tracks ([rekordbox-analysis.md](dev_docs/rekordbox-analysis.md)).

### Linux and Windows

- [ ] Burn an MP3 disc from `dubcrate export`'s `disc.iso` (Brasero, Finder, `cdrdao`) and play it in a car player ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Try the Linux drive permission message on a real machine (`cdrdao.access_problem`; stand-ins only).
- [ ] Check the Explorer's real drive lists on Linux and Windows, which are only tested with fake folders ([cross-platform.md](dev_docs/cross-platform.md)).
- [ ] Windows burning needs an administrator account (a standard user can't open the drive): Dubcrate says so; an elevated burn (a UAC prompt) is not built.
- [ ] Check what `pyrekordbox`'s SQLCipher needs on Intel Linux (Windows passes its smoke test) ([cross-platform.md](dev_docs/cross-platform.md)).

## Completed

### General

- [x] Docs pass (2026-11-24): README, user guide and changelog rewritten short and in plain English (the guide is 36% shorter, the changelog 75%); Windows and Linux burning, USB sticks, XML and the track finder are in; the OS wording is fixed (⌘ is Ctrl on Windows and Linux, Windows needs an administrator account to burn, the AppImage and installer bundle `cdrdao`); `CONTRIBUTING.md` brought up to date. The guide's own tests still pin its menu names, shortcuts and settings.
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
- [x] Windows burn support: a real audio CD with CD-TEXT and an MP3 disc burn through the bundled `cdrdao.exe` (2026-11-24, see Linux and Windows below).
- [x] Bundled ffmpeg on Linux and Windows too (the AppImage and the installer carry ffmpeg, ffprobe and `cdrdao`).

### Hardware checks (Mac and the CDJ-1000MK3)

- [x] MP3 cue timing got much closer (2026-11-24): the player counts the Info frame for any file that has one, so ffmpeg-made files are placed with their own lead ([cue-timing.md](dev_docs/cue-timing.md)).
- [x] Paint the zoomed waveform without long GIL holds (2026-11-16: the 3-band look is a numpy image now, 3.4 ms a frame; no late audio callbacks at 128 or 256 frames offscreen) ([audio.md](dev_docs/audio.md)).
- [x] Listen to previews at 256 and 128 frame buffers after the audio fixes and decide the default buffer size ([audio.md](dev_docs/audio.md)).
- [x] Investigate 60 FPS waveform scrolling (2026-11-16, `spikes/waveform_fps.py`, everything in [waveform-60fps.md](dev_docs/waveform-60fps.md), short version in [audio.md](dev_docs/audio.md)): possible, worth doing; the 3-band look takes 16 ms a frame (the whole 60 FPS budget) because of three polygon fills, so it needs a cheaper renderer first (a numpy image: 2 to 5 ms).
- [x] Build 60 FPS scrolling (2026-11-16): an image-based 3-band look (16 ms -> 3.4 ms a frame, checked against the polygons in dark, light and System 6), the position moving between audio blocks, a 16 ms tick, and Settings > Appearance > Waveform speed (Smooth 60 / Light 30). No late audio callbacks at 128 or 256 frames offscreen ([waveform-60fps.md](dev_docs/waveform-60fps.md), "What was built").
- [x] Confirm a burned disc is really gapless (tracks at 00:02.00, 00:42.00, 01:12.00 in `drutil toc`) now that the 2 s pregap fix is in ([burning.md](dev_docs/burning.md)).
- [x] Check the new Peak to full scale preset on the CDJ for clipping at -0.3 dBTP ([roadmap.md](dev_docs/roadmap.md)).
- [x] Try the 60 FPS waveform by eye and ear on the Mac (smooth scroll, in step with the sound, no tearing; the Light setting; a 120 Hz display). If it judders, follow `QScreen.refreshRate()` or take the position at paint time.
- [x] Try the things only simulated so far by hand: the Explorer (permission prompts, slow or unplugged drives), pinned tab dragging, drag-to-change spin boxes, trackpad pinch in the label preview and mouse drag and drop ([roadmap.md](dev_docs/roadmap.md)).
- [x] Look at the app on a real display in all four looks, including the splash and the label window in System 6 ([roadmap.md](dev_docs/roadmap.md)).
- [x] MP3 sync without playing the disc first (2026-11-24): the CDJ's disc ID is decoded from the firmware (`Mp3DiscId.made_from`); a burn pins and keeps the image date; Sync makes the card's index entry, and a CDJ took one on a card with no MPJ folder. Disc ID… window (Discs and Sync lists) records an already-burned disc from the drive or an image file, checked on the real disc ([card-sync.md](dev_docs/card-sync.md)).
- [x] Almost full MP3 disc with four folders (2026-11-24, 64 files, 350,179 of 359,846 sectors, burned with the new version via `cdrdao`): the CDJ plays it, the size estimate held, and Sync made the card's entry from the burn's own record with no play: waveforms appear instantly on the CDJ ([card-sync.md](dev_docs/card-sync.md), [burning.md](dev_docs/burning.md)).

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

- [x] Try the rekordbox USB stick in the running app (it is only tested offscreen and from scripts): import it, unplug and replug it, plug it back as a second mount. Check the "(unplugged)" row, All Tracks without its tracks, the Show Tracks from Unplugged Drives tick, and the right-click Re-import / Remove. Is the Remove confirmation right for a stick? ([rekordbox-usb.md](dev_docs/rekordbox-usb.md)). Sticks with only `export.pdb` are refused by decision. (Ticked off by the owner, 2026-11-26.)
- [x] Try disc and card auto-detection on macOS and Windows (Linux works; Windows listed a card with a PIONEER folder, and a blank one after a fix, 2026-11-24). (Ticked off by the owner, 2026-11-26.)
- [x] Import Cues from an MP3 card notices cues deleted or moved on the player (2026-11-26): every MP3 sync (the Sync tab and `dubcrate card sync --yes`) records a baseline (the cues the card holds, keyed by the burned image's numbers, per (folder, file)), and the review offers deletions and moves as for an audio disc. Tested end to end on synthetic cards, **not tried on a player** ([card-sync.md](dev_docs/card-sync.md)).
- [x] Unplugged rekordbox USB sticks (2026-11-26): their tracks stay out of All Tracks and the search (a tick in the right-click menu brings them back; a stick that is the only source is never hidden), the tree row is dimmed and says "(unplugged)", and a source has a right-click menu with Re-import and Remove. Tested offscreen only.
- [x] Read a rekordbox USB stick (2026-11-24: `rekordbox_usb.py`, `dubcrate import-usb`, schema v20; the player, cue cache, Sync and the finder read it; File > Import rekordbox USB; an unplugged stick's tracks are marked "file not found" and back; a stick that returns under another name is recognised) ([rekordbox-usb.md](dev_docs/rekordbox-usb.md)).
- [x] Finder page: **By disc** (the tagged tracks under a heading per disc) and **Share list** (the phone's share sheet, else copy, else the text in a box) (2026-11-24; node and headless Brave only: try the share sheet and copy in the viewer app on the phone). The played tick is still not built.
- [x] Try `tinytag` as a faster folder scan (2026-11-24: tried, **not adopted**: 2.7x faster than ffprobe at best, and it misread one file's duration, 14.6 s for 337.6 s, on 400 real files; [roadmap.md](dev_docs/roadmap.md)).
- [x] Rekordbox XML as an import path with no SQLCipher dependency (2026-11-24: `rekordbox_xml.py`, source kind `rekordbox_xml` (schema v21), File > Import rekordbox XML…, `dubcrate import-xml`; tracks, playlists and cues, no waveform or beat grid; read from the owner's own Dubcrate export, not from a rekordbox-made file: [rekordbox-xml.md](dev_docs/rekordbox-xml.md)).
- [x] The burn window greys out speeds above the drive's top writing speed (`cdrdao drive-info`: 24x on the test drive; it offered 48x and `cdrdao` quietly wrote at 24x). Tried on the real drive: 4234 kB/s reads as 24x; the window itself is untried.
- [x] The Sync tab's middle pane lists an MP3 disc's tracks (folder, artist, length) with the picked track's waveform, as for an audio disc.
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
- [x] Finder page on iOS (2026-11-24): drag-reorder, Filter, the waveform and cue view and Clear work on the owner's phone with the real library; the first waveform export takes about 5 seconds for 400 tracks (on a Mac); un-tagging a played track with the star works as the played mark.

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

- [x] Run the Linux AppImage on an older glibc (needs 2.39+), Ubuntu or Fedora ([linux-test-plan.md](dev_docs/linux-test-plan.md)). (It works on the owner's Arch desktop, 2026-11-24, a build made here.) (Ticked off by the owner, 2026-11-26.)
- [x] Install the new installer over an older one on Windows: does the upgrade keep the library and settings? Does Settings > Library show the bundled ffmpeg? (A VM installs, runs and uninstalls it, 2026-11-24.) (Ticked off by the owner, 2026-11-26.)
- [x] Try the disc TOC reader and disc-in-drive detection on Windows (`drive_toc.py`; eject works, and the burn's read-back goes through `cdrdao`) ([cross-platform.md](dev_docs/cross-platform.md)). (Ticked off by the owner, 2026-11-26.)
- [x] Format a Card on Windows: also try a card in the laptop's own slot, and a second format over a card the player has written (the first works and the CDJ accepts it). (Ticked off by the owner, 2026-11-26.)
- [x] cdrdao or `xorriso -as cdrecord` for MP3 discs: decided, `cdrdao` (2026-11-24: it burned an audio disc with CD-TEXT and two MP3 discs on the CDJ test drive, and is bundled in the AppImage); `xorriso` is not needed.
- [x] The Linux AppImage runs on a real desktop (Arch, Wayland, 2026-11-24, built here with `cdrdao` bundled): opens, plays, burns and syncs.
- [x] The test suite on Windows 11 (a VM here, 2026-11-24): 2,720 tests, one failure (a `str(Path('/dev/sr0'))` in a test: Windows spells it with backslashes), fixed; the two earlier causes stay fixed.
- [x] Windows build, first real run (2026-11-24, a Windows 11 VM): ffmpeg and LAME build in MSYS2; `cdrdao` builds in MSYS2's MSYS shell (the Cygwin-compatible one; a native MinGW build fails on its Unix calls) and is bundled with `msys-2.0.dll` and `msys-iconv-2.dll`; Nuitka needs the Visual Studio Build Tools; the installer installs without a prompt, passes the smoke test installed, and uninstalls. Found and fixed: `git describe` on a repository with no tags stopped the script under Windows PowerShell 5.1.
- [x] Windows drive detection and simulated burns (2026-11-24, USB passthrough to a Windows 11 VM): Dubcrate lists the drive (`D:\`, top speed 24x) and simulates an MP3 disc and an audio CD with CD-TEXT. Needed two fixes: a drive letter counts as a device, and TOC file names are written as `/cygdrive/c/...` for the Windows `cdrdao`.
- [x] Windows: a real audio CD with CD-TEXT burns and its TOC checks out; the PDF labels export; a card synced through a USB card reader passed to the VM works on the CDJ (2026-11-24). Found: the eject script's `Test-Path` threw "access denied" as the card left, so an eject that had worked was reported as a failure (fixed: the error means the drive is leaving). Still to see: the fixed eject on a real card, and the second message ("no CDJINDX0.PDJ found") after a failed eject, which a guard now avoids but was not reproduced.
- [x] Windows, more real checks (2026-11-24, a Windows 11 VM with the burner and a card reader passed through): audio CD with CD-TEXT and a one-folder MP3 disc burn and play on the CDJ-1000MK3, the MP3 disc syncs without a play first, a card formatted in the app works in the player, previews play with no crackle at the default buffer, the PDF labels export. Found: a standard user can't use the drive; a path over 259 characters reads as "file not found" (now explained).
- [x] Linux audio CD and MP3 CD burns, both on the CDJ-1000MK3 (owner, reported 2026-11-24): both written with `cdrdao` on Linux, with no permission trouble; both load in the player and the CD-TEXT appears as expected. The MP3 disc: loaded a little slower than the audio disc (not very noticeable), the display showed the ID3 tags, the file order was Dubcrate's order, and no track had trouble playing, so ffmpeg's Info frame is fine (`-write_xing 0` not needed). **Without pre-written waveforms the player drew them at about real-time speed; the waveforms Dubcrate wrote to the card removed that wait.** This finishes "Try on a real Linux machine + drive", the MP3 disc's CDJ check and the `cdrdao` drive-access check ([linux-test-plan.md](dev_docs/linux-test-plan.md), [burning.md](dev_docs/burning.md)).
- [x] Finder page, Tagged in the iPhone viewer app Sitecase (owner, reported 2026-11-24): the tagged tracks survive closing the app, so `localStorage` is kept there (before, only checked in headless Brave and node).
- [x] MP3 disc sync, waveforms (2026-11-23): MP3 discs are rows of the Sync tab's list and `dubcrate card sync` takes them; the owner's real card and the CDJ show the waveforms. The first real sync kept the player's near-empty records for tracks it had only started, so most tracks stayed blank; fixed by completing such a record column by column. Also fixed from it: the card row's disc count includes MP3 discs (25 became 26), and a code review's findings (ticks surviving a rebuild, a right-click on another card, a failed write explained, the burn window's drive probe off the GUI thread, and more).
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
