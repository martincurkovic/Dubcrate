# Rekordbox: what we read and what we found

Tagged like [burning.md](burning.md): **[measured DATE]** on the owner's real rekordbox 7
library (1,065 tracks; 300-400 sampled), **[notes]** from Deep Symmetry's format notes
and not checked, **[tests]** covered by the automated tests. Everything here is
read-only: Dubcrate never writes to rekordbox.

## The database (`master.db`)

- **[measured 2026-11-11]** `~/Library/Pioneer/rekordbox/master.db`, SQLCipher, with
  `master.db-wal` / `-shm` sidecars. Never open it in place: `library/rekordbox.py`
  copies it (and the sidecars, and `masterPlaylists6.xml`) to a temp dir and reads the
  copy.
- **pyrekordbox 0.5.0**: use `pyrekordbox.masterdb.MasterDatabase`
  (`Rekordbox6Database` is deprecated and goes in 0.6.0). The key resolved by itself on
  this machine, with the `sqlcipher3-wheels` binary wheel. It logs a warning if
  `masterPlaylists6.xml` isn't beside the db (harmless). Bundling SQLCipher with Nuitka
  has **not** been tried.
- `djmdContent`: `ID` is a string; `FolderPath` is the **full file path**; `BPM` is
  x100 (14300 = 143.00); `Length` is whole seconds (so lengths are approximate until
  measured); `KeyName` is already Camelot-style here; `AnalysisDataPath` is relative to
  `<rekordbox folder>/share`.
- `djmdPlaylist`: `ParentID` is `"root"` at the top; `Attribute` 0 = playlist, 1 =
  folder, 4 = smart, -128 = system; `rb_local_deleted` marks soft-deleted rows. System
  rows to skip: `100000` ("Trial playlist - Cloud Library Sync") and `200000`
  ("CUE Analysis Playlist"). Smart playlists store a rule, not members, so they are
  skipped (and counted in the import notes). [tests]
- `djmdSongPlaylist`: `PlaylistID`, `ContentID`, `TrackNo` gives the running order.

## Analysis files

- **[measured 2026-11-11]** All 400 sampled tracks had `.DAT`, `.EXT` and `.2EX`, at
  `<rekordbox folder>/share/PIONEER/USBANLZ/<3 hex>/<uuid>/ANLZ0000.{DAT,EXT,2EX}`.
- Tags (pyrekordbox `AnlzFile`): `.DAT` PQTZ (beat grid), PWAV/PWV2 (small mono
  previews), PCOB (cues); `.EXT` PWV3 (mono detail), PWV5 (colour detail), PWV4
  (colour preview), PCO2 (cues), PQT2, PSSI (phrases); `.2EX` **PWV7 (3-band detail)**,
  **PWV6 (3-band preview)**, PWVC.
- **Detail waveforms run at 150 columns per second** (a 295 s track has 44,305). The
  3-band ones (`PWV7`, 3 bytes per column, `PWV6` 1,200 columns) come back from
  pyrekordbox as raw bytes; `waveform.from_bytes` decodes them. Values are 0-127.
- **Byte order is low, mid, high** (byte 0 tracks the low band; means over one track
  45 / 34 / 14). Checked by correlating with bands computed from the audio. Our own
  computed waveform correlates 0.82 with rekordbox's low band and about 0.47 with mid
  and high: close enough to look alike, not identical. [tests] cover the decoding.
- `PWV6` (the 1,200-column preview) looks high-dominated when drawn, so the overview
  and the track list's Preview column use `PWV7` averaged down instead.
- **Blue and colour waveforms: what they hold and how our looks relate.**
  **[measured 2026-11-11]** `.EXT` also has `PWV3` (the blue detail waveform: per column a
  height 0-31 and a whiteness 0-7) and `PWV5` (the colour detail waveform: a height and
  three colour channels), at the same 150 columns/s, column for column with `PWV7`
  (checked on 60 tracks). Dubcrate does not read them: the RGB and blue looks are
  derived from the 3-band bands (`waveform.py`), so they work for every track. Across 60
  tracks, per-track Pearson r between the native data and the 3-band bands:
  - blue **height** follows the RMS of the three bands best (0.86; mean 0.85, max 0.81);
  - the colour waveform is **red = lows, green = mids, blue = highs** (R vs the low share
    of the column 0.73, G vs mid share 0.69, B vs high share 0.60): hue is the column's
    make-up, not its loudness;
  - blue **whiteness** is the opposite of bass: r = -0.74 with the low share, +0.69 with
    the mid+high share.

  The derivation has two fitted constants: per-channel gains (1.0, 0.5, 1.4) for the colour
  (raw shares over-weight green and under-weight blue; mean absolute error 0.247 down to
  0.192), and a whiteness stretch so its spread matches rekordbox's. **Held-out check** on 59
  other tracks, with the functions as shipped: height r = 0.87, whiteness r = 0.68, colour
  r = 0.52 / 0.78 / 0.62 (R / G / B), mean absolute error 0.195. So: similar, not identical,
  and red is the weakest channel. Reading `PWV3` / `PWV5` directly would match rekordbox
  exactly for its own tracks; it would still need the derived look for folder tracks.
- **Beat grid** (`PQTZ`): beat number in the bar (1-4), tempo, time in seconds; 640
  entries for a 295 s, 130 BPM track.
- **Phrases** (`PSSI`): `mood`, `end_beat`, and entries of (`beat`, `kind`), with beats
  1-based and converted to time through the beat grid. **Mood 1 ("high") was 278 of 300
  tracks**; its kinds are 1 Intro, 2 Up, 3 Down, 5 Chorus, 6 Outro, and a 15-phrase
  track came out exactly as rbxport shows it **[measured]**. Mood 2 (21 tracks) and 3
  (1 track) use kinds up to 10, named from the format notes **[notes]**, unchecked.
- **Cues: none of the 300 sampled tracks had any**, so the parsing of `PCO2` / `PCOB`
  entries (field names `time`, `hot_cue`, `loop_time`, `type`, `comment`) is defensive
  and only [tests]-checked with fake entries. Verify on a track that has cues.
- **Speed:** a full `.EXT` parse is about 54 ms with pyrekordbox; `read_analysis` for a
  track is about 57 ms (fine on a worker thread); reading just the preview from
  `.2EX` is about 0.4 ms, which is why the Preview column can be filled for the rows on
  screen without a worker.
