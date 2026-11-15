# Burning: what has been tried on hardware

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

## Trying a burn from the command line

`dubcrate burn` (and `dubcrate doctor`, which says what Dubcrate finds: tools, drives, the disc in the
drive) is the way to try a burn without the window. It uses a library, so use a scratch one
(`--library /tmp/x.db`) and never your own. It is a dry run unless you pass `--yes`.

```sh
dc() { uv run dubcrate --library /tmp/x.db "$@"; }
dc doctor                                     # tools, drive, media state
# three synthetic tracks: 24-bit FLAC at 48 kHz, a WAV with a Unicode title, an MP3
mkdir -p /tmp/tones
ffmpeg -f lavfi -i sine=frequency=440:duration=40:sample_rate=48000 -ac 2 -c:a flac -sample_fmt s32 -metadata title="Test Tone A (24-bit/48k)" -metadata artist=Dubcrate /tmp/tones/01-a.flac
ffmpeg -f lavfi -i sine=frequency=523:duration=30 -ac 2 -c:a pcm_s16le -metadata title="Café Ünïcode ☆ Test" -metadata artist="Dubcrate Ünit" /tmp/tones/02-b.wav
ffmpeg -f lavfi -i sine=frequency=659:duration=25 -ac 2 -c:a libmp3lame -b:a 320k -metadata title="Test Tone C (MP3)" -metadata artist=Dubcrate /tmp/tones/03-c.mp3
dc import-folder /tmp/tones
dc disc new "Tones" --pregap 0
dc disc add 1 --track 1 2 3
dc burn 1                                     # dry run: checks the disc, burns nothing
mkdir -p /tmp/tones-out && dc export 1 /tmp/tones-out   # a new folder there with the WAVs and the cdrdao TOC, no drive needed
dc burn 1 --yes --simulate                    # laser-off test write, needs a disc in the drive
dc burn 1 --yes                               # really burn
```

`--pregap` on `disc new` / `disc set` sets the silence before tracks 2 and up (see the findings below for what
the drive accepts), `--backend discrecording|cdrdao` picks the burner, and `--work DIR` keeps the rendered WAVs.

## A hardware check, step by step

Use a blank CD-R (not a CD-RW) and a low speed.

1. Put the disc in, run `dubcrate doctor`: it should show the disc as blank, and the drive as
   able to write CD-R, CD-TEXT, SAO and test-write.
2. Run `dubcrate burn 1 --simulate --yes`. The laser stays off, so the disc survives.
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
- **[hardware 2026-11-15]** The fix as first written (`DRPreGapLengthKey` 0 with `DRPreGapIsRequiredKey` true on
  tracks 2+) **failed on the LiteOn eSAU108**: "The disc drive doesn't support the requested pregap length",
  before any track was written (the drive ejects the disc on a failed burn). Test writes (laser off) showed:
  length 0 with the flag **off** is accepted; length 0 required is refused, even on every track. `build_tracks`
  now leaves the flag off, and the waveform test disc (20 tracks) was burned that way. **It is gapless, 2026-11-15:** `drutil toc` shows track 1 at
  00:02.00 and every 100 s track exactly 100 s after the last (1:42.00, 3:22.00, ... lead-out 34:02.00), and
  `dubcrate card toc 1` says the planned TOC is exactly the disc's (so `toc_from_lengths` holds for a real burn:
  the card-sync disc ID question, gap (d), is answered for this disc). The CLI doesn't run `burn_check`.
  Non-zero values with the flag on were not reached (the disc ejected after the refusal).
- **[not tried, superseded above]** The fix: discs are now gapless by default (`pregap_sectors = 0`,
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
- **[tests]** Folders (`disc_dir`, schema v8): an MP3 disc's tracks each live in a folder or the
  root, and `render_plan` makes the same directories, numbering files from 1 in each, with empty
  folders kept. The limits are Pioneer's, as researched by the owner: 999 files per folder, 99
  folders counting the root, 8 levels counting the root (`mp3.MAX_*`); the store refuses to go past
  them. "8 layers deep" was read as including the root, the safer reading: if the CDJ really allows
  8 folders below the root, raise `MAX_DEPTH` to 9.
- **[not tried]** Folders on the CDJ: whether it shows them as the disc has them, whether a
  folder of 999 files and 8 levels really load, and in which order it lists folders against the
  files in the root. A small first test: two folders, one inside the other, three files each.
- **[tests]** Constant bitrate only (the owner's rule: VBR files load slowly on a CDJ-1000MK3).
  `mp3.scan_mp3` reads every frame header (the Xing/Info tag is not trusted: plenty of CBR files
  have none), and `render_mp3` copies an MP3 only if it is CBR at or under the disc's ceiling,
  otherwise encodes constant: lossless at the ceiling (320 by default), a lossy source below it at
  its own rate rounded up to a standard step (245 -> 256, 128 -> 128). Everything it writes is
  scanned again and refused if it is not CBR. Against real ffmpeg output: CBR and VBR files, one
  with an ID3v1 tag, AAC, FLAC, and noise at a high VBR rate.
- **[not tried]** That this is what makes a CDJ load an MP3 disc quickly (the owner's finding, not
  measured here), and whether the "Info" header frame ffmpeg writes in each file is harmless to the
  player. If a file's first moment is silent or the load is still slow, try `-write_xing 0`.
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
  loop of the thread it runs on. It has only been run on the main thread (the first spike, since removed).
  Whether DiscRecording is happy being driven from a worker thread is untested; if
  it isn't, start and poll the burn on the main thread with a `QTimer`.
