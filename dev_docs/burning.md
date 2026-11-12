# Burning: the spike, and what has been tried on hardware

Notes are tagged by how we know: **[hardware DATE]** measured on the real drive or
player, **[tests]** covered by the automated tests, **[not tried]** written but never
run. Update this file when something is checked.

## The burn pipeline

1. Tracks are decoded and rendered to 16-bit / 44.1 kHz stereo WAV with ffmpeg, with
   dither when reducing bit depth, and padded with silence to a whole CD sector
   (588 samples) so track boundaries fall where the table of contents says.
2. A `Burner` backend writes the disc session-at-once (disc-at-once for audio),
   with CD-TEXT for the disc and every track.
   - `burn/discrecording.py`: Apple's DiscRecording framework through PyObjC.
   - `burn/cdrdao.py`: builds a cdrdao TOC and runs `cdrdao`.
3. Capacity is checked before rendering (from measured lengths) and again after
   (from the rendered lengths, which are the truth).

## The burn spike

`spikes/burn_spike.py` is the original hardware driver: it renders tracks from files
and burns them without the library. It is a dry run unless you pass `--yes`.

```sh
uv run spikes/burn_spike.py doctor                 # tools, drive, media state
uv run spikes/burn_spike.py tones work/tones       # synthetic test tracks (24-bit FLAC, WAV, MP3)
uv run spikes/burn_spike.py plan work/tones/* --work work/render
uv run spikes/burn_spike.py toc  work/tones/* --work work/render     # the cdrdao TOC
uv run spikes/burn_spike.py burn work/tones/* --work work/render \
    --backend discrecording --yes --simulate       # laser-off test write, needs a disc in the drive
uv run spikes/burn_spike.py burn work/tones/* --work work/render \
    --backend discrecording --yes                  # really burn
```

`--pregap SECONDS` adds silence before tracks 2 and up. `work/` is git-ignored.

## A hardware check, step by step

Use a blank CD-R (not a CD-RW) and a low speed.

1. Put the disc in, run `doctor`: it should show the disc as blank, and the drive as
   able to write CD-R, CD-TEXT, SAO and test-write.
2. Run the burn with `--simulate --yes`. The laser stays off, so the disc survives.
3. Burn for real.
4. `drutil toc` on the burned disc. With gapless discs, track *n+1* should start
   exactly where track *n* ends (for the three test tones: 00:02.00, 00:42.00,
   01:12.00).
5. Play it in the CDJ: track times, CD-TEXT titles, gaps between tracks.

## Findings

- **[hardware 2026-11-11]** The drive is a LiteOn eSAU108 (`slimtype eSAU108`, USB).
  macOS labels it "Unsupported", but DiscRecording reports it can write CD-R,
  CD-TEXT, SAO (session-at-once) and test-write. Offered write speeds: 10x, 16x,
  20x, 24x (so 8x is not available).
- **[hardware 2026-11-11]** A simulated (laser-off) burn through DiscRecording
  completed, and the disc ejected blank.
- **[hardware 2026-11-11]** A real burn of three tones through DiscRecording
  completed with no errors. On a CDJ-1000MK3 the track times were right and the
  **CD-TEXT displayed correctly, including accented characters** (`é`, `Ü`, `ï`).
  A character outside Latin-1 (`☆`) is replaced by `?`, as designed.
- **[hardware 2026-11-11]** The same disc had a slight gap between tracks.
  `drutil toc` showed tracks starting at 00:02.00, 00:44.00 and 01:16.00: a
  **2-second pregap** after tracks 1 and 2. Cause: DiscRecording's default pregap
  is 2 s of silence (`DRPreGapLengthKey`), which we hadn't overridden.
- **[not tried]** The fix: discs are now gapless by default (`pregap_sectors = 0`,
  and `DRPreGapIsRequiredKey` set so a drive that can't do it fails the burn
  rather than silently falling back to 2 s). Expected after the fix: tracks at
  00:02.00, 00:42.00 and 01:12.00. [tests] cover the planning and capacity side.
- **[not tried]** The `cdrdao` backend has never been run (cdrdao isn't installed on
  the dev machine). Its TOC generation is covered by [tests].
- **[not tried]** A non-zero gap. The unit of `DRPreGapLengthKey` isn't documented;
  we treat it as sectors (its 2-second default is 150). Only 0 has been used, and
  the window offers 1, 2 and 3 s, so check the unit before relying on those.
- **[not tried]** Reading the inserted disc's capacity. `DiscRecordingBurner.media_info`
  takes `DRDeviceMediaBlocksFreeKey` (already printed by `doctor` as
  `media_free_blocks`) as the room for audio, and `burn` refuses a disc that needs
  more. Unknown whether that figure is in the same sectors as our track lengths, or
  includes the 150-sector lead-in gap, on a blank CD-R. Next time the drive is
  attached, run `doctor` with an 80 and a 74 minute disc and compare (expect about
  359,850 and 333,000). The checks are deliberately cautious: unknown or 0 free blocks
  is never a reason to refuse, a disc that "isn't blank" only prints a note, and the
  `cdrdao` backend reports everything unknown.
- **[not tried]** Burning from the window (`ui/burn_dialog.py`). The dialog is tested
  only with a fake burner. What is unverified on hardware: DiscRecording's
  `media_info` and `burn` called from a **worker thread** (the CLI runs them on the
  main thread; `burn` pumps the run loop itself, and whether that is fine off the main
  thread is unknown), the drive and disc shown in the window, and the **Test only**
  option. If a window burn hangs or the progress log stays empty, suspect the thread
  first and compare with `dubcrate burn`. Run a `--simulate`-style **Test only** from
  the window before the first real one.
- **[tests]** CD-TEXT cleaning (Latin-1 kept, others folded or `?`), TOC escaping,
  sector maths, and the packing algorithms.

## MP3 CDs

An MP3 disc (`disc.kind = "mp3"`) is staged by `build.render_plan` as a folder of numbered
MP3s (`render.render_mp3`: an MP3 is copied with fresh tags, anything else encoded with
libmp3lame at the disc's bitrate) and written by `DiscRecordingBurner.burn_data` as one data
track: `DRFolder` with `explicitFilesystemMask` ISO 9660 | Joliet, `DRTrack.trackForRootFolder_`.

- **[tests]** Sizes and file names, the library and conversion, staging with real ffmpeg, the
  burn window with a fake burner, and that DiscRecording builds the data track from a folder
  without a drive (its `estimateLength()` for one tiny file is 178 sectors, which is where
  `FILESYSTEM_OVERHEAD_SECTORS` comes from).
- **[not tried]** Everything that touches the drive or the player. Check, with a CD-R you can
  spare, in this order:
  1. A **Test only** burn of a small MP3 disc from the window. It exercises the data-track
     path (and that DiscRecording takes a data track from a worker thread, as for audio).
  2. A real burn of a few tracks (one MP3, one FLAC). Mount the disc on the Mac: are the
     files there, in order, with their tags? `drutil status` / `diskutil info` shows the
     file system. Joliet names should be long; the ISO 9660 names are DiscRecording's own
     8.3 forms.
  3. The CDJ-1000MK3: does it read the disc and how long does it take to load, which order
     does it play in (the name prefix is meant to make that the disc's order), what does it
     show (the ID3 title and artist, or the file name), and is anything refused (the
     bitrate, the sample rate of a copied MP3, a name Joliet allows but the player doesn't)?
  4. The `DRBurnStrategy` for a data track is left to DiscRecording; if a burn fails to
     start, try `DRBurnStrategyCDTAO` or `CDSAO` in `burn_data`.
- **[not tried]** How near the limit a disc can get: `used` is the sum of the files plus the
  estimated overhead, checked again against the drive's free blocks. Try a nearly full one.
- **[not tried]** The CDJ's limits on files per disc and folder depth (this puts every file
  in the root, so only the file count could matter). `MAX_TRACKS` is not applied to MP3 discs.

## DiscRecording API notes

- A burn is configured with `DRBurn.burnForDevice_(device)` then `setProperties_`, and
  started with `writeLayout_(tracks)`, which returns **immediately**; poll
  `burn.status()` until `DRStatusStateKey` is `DRStatusStateDone` or `...Failed`. A
  `DRStatusPercentCompleteKey` of -1.0 means "indeterminate", not an error.
- Tracks: `DRTrack.trackForAudioFile_(path)`. Strategy: `DRBurnStrategyCDSAO` with
  `DRBurnStrategyIsRequiredKey`. Speed is in KB/s: `DRDeviceBurnSpeedCD1x` times the
  "x" factor.
- CD-TEXT: the `DRCDTextKey` burn property takes a `DRCDTextBlock`
  (`initWithLanguage_encoding_("en", DRCDTextEncodingISOLatin1Modified)`), with the
  disc's own text at **track index 0** and track *n* at index *n*
  (`setObject_forKey_ofTrack_`). Check `DRDeviceCanWriteCDTextKey` first.
- Pregap: `DRPreGapLengthKey` on a `DRTrack`, default 2 s; with
  `DRPreGapIsRequiredKey` true the burn fails instead of picking another length.
- **Not yet known (matters for the burn dialog):** the polling loop pumps the run
  loop of the thread it runs on. It has only been run on the main thread (the spike).
  Whether DiscRecording is happy being driven from a worker thread is untested; if
  it isn't, start and poll the burn on the main thread with a `QTimer`.
