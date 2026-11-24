# Reading a rekordbox USB stick (2026-11-24)

The "read a rekordbox USB stick" item. **Read-only on the stick, always.** Step 1 (the reader and the command line) and step 2 (everything
that reads rekordbox's cues and analysis can read a stick) are built, 2026-11-24; step 3 (the window's File > Import rekordbox USB) is built too, 2026-11-24. All three are done.

## The decisions (the owner, 2026-11-24: "defaults")

1. A stick is its own source kind, `rekordbox_usb` (schema v20). The code that WRITES to rekordbox (`rekordbox_write`,
   `ui/rekordbox_editor.py`) asks for `kind == "rekordbox"`, so it can never see a stick. Anything that reads a rekordbox
   source's cues, analysis or `master.db` path has to be given a per-kind reader before it can handle a stick (step 2).
2. `exportLibrary.db` only ("OneLibrary", rekordbox 7). A stick with only the older `export.pdb` is refused in words.
3. What is read: tracks (title, artist, album, genre, year, BPM, key, rating, label, remixer, composer, My Tags,
   play count, date added, comment), playlists and folders, and cues (memory, hot, loops), plus each track's `USBANLZ` path.
4. An unplugged stick: the start-up re-read only refreshes `kind == "rekordbox"`, so a stick is left alone and its tracks stay listed. **(The first version of this note said its files "show as missing": they did not. `track.missing` is only set during an import, so an unplugged stick looked normal and failed when a track was played or burned. Fixed in "Leftovers" below.)**
5. How one is imported: `dubcrate import-usb PATH` (the root, its PIONEER folder or the database). The window's
   "Rekordbox USB…" with a list of detected sticks (`rekordbox_usb.find_sticks`) is step 3.

## The format, read off the owner's stick (HFS+, 1,109 tracks, 124 playlists, 4,423 cues)

- `ROOT/.PIONEER/rekordbox/` (`PIONEER` on a stick that wasn't hidden; both are accepted) holds `export.pdb` (the old
  database, not read), `exportExt.pdb`, `exportLibrary.db`, `playlists3.sync`. Music is in `ROOT/Contents/`, analysis in
  `ROOT/.PIONEER/USBANLZ/Pxxx/xxxxxxxx/ANLZ0000.DAT|EXT|2EX` (the files `analysis.py` already reads, with `ROOT` as the root).
  `.rbxport-write.lock` at the root is the owner's other app (rbxport) managing the stick.
- `exportLibrary.db` is SQLCipher with a key pyrekordbox knows (`pyrekordbox.onelibrary.OneLibrary`, which needs the
  `sqlcipher3` package that is already a dependency). Tables: `content`, `artist`, `genre`, `key`, `album`, `label`,
  `cue`, `playlist`, `playlist_content`, `my_tag(_content)`. **Paths are from the stick's root**: `/Contents/Artist/Track.mp3`,
  `/.PIONEER/USBANLZ/P002/0002041C/ANLZ0000.DAT`.
- `content.bpmx100` is BPM x 100 and `length` whole seconds; `playlist.attribute` 0 is a playlist and 1 a folder,
  `playlist_id_parent` 0 is the top. rekordbox's empty "CUE Analysis Playlist" is there (twice): it is left out.
- `cue.kind` is `master.db`'s: 0 memory, 1 to 3 hot A to C, 5 to 7 hot D to F (counts on this stick: 893, 1079, 1042, 952, 454, 2, 1); a row whose
  `outUsec` is after `inUsec` is a loop (8 of them); times are `inUsec` / `outUsec` in microseconds (-1: none). A cue with no time is dropped.
- Cue times are rekordbox's (later than the audio on a disc for most MP3s: `cue_timing`); step 2 has to convert them as
  `RekordboxCues` does for `master.db`.
- **Measured here:** a stick read in 0.4 s (copy + read); every track's file and its analysis file exist; `analysis.read_analysis(ROOT, path, duration)`
  gave a beat grid (1,061 beats) and a waveform for the first track.

## The code

`library/rekordbox_usb.py`: `stick_root`, `library_file`, `find_sticks(volumes)`, `read_usb(path, progress, cancel)` ->
`RekordboxLibrary` (its `source` is the stick's root, which is the source's path), `read_usb_cues(path)` -> `{content id: [Cue]}`,
`track_from_content`, `build_nodes`, `cue_from_row`. The database is **copied to a temp folder and the copy is read**; the module
has no write, and `tests/test_library_rekordbox_usb.py` checks that the stick's files and their times are unchanged after a read,
that the source has no `commit(` / `.add(` / `delete(`, and that nothing is moved or removed. `library/store.py: _migrate_to_v20` relaxes the
`source.kind` check by editing its stored definition (`PRAGMA writable_schema`: SQLite's documented way for a CHECK that is only relaxed;
rebuilding the table would delete every track through the foreign-key cascade) and is tested on a v19 library with data.
CLI: `dubcrate import-usb PATH [--name N]`; `dubcrate sources` lists the kind.

## Step 2: a stick's tracks where rekordbox's data is read (2026-11-24)

`library/rekordbox_sources.py` is the ONE place that says what differs per source kind (`KINDS`, `carries_rekordbox_data`, `analysis_root`,
`cue_database`, `read_cues_at`); everything that READS rekordbox's cues, beat grids or waveforms asks it instead of testing `kind == "rekordbox"`:
`card_discs.RekordboxCues` (Sync, the card, Import Cues, the finder), `ui/cue_cache.py` (the player, the track list's cue flags: `cue_file_for`),
`ui/preview.py` (`_analysis_root`: the beat grid, phrases and waveform), `ui/main_window.py` (the track list's Preview column), `track_finder.py`
(waveforms, beat grids, cue tables) and the Sync tab's cue loader. `Library.rekordbox_twin` includes sticks, so a file browsed in the Explorer
(or a folder source) at a stick's path takes the stick's BPM, key, cues and analysis. `read_cues_at(path, master_reader)` keeps each module's own
`read_cues` as the seam tests replace. The window's Re-import handles a stick (`start_import("rekordbox_usb", ...)`).

**What stays master.db-only, on purpose, and is tested (`test_nothing_that_writes_to_rekordbox_knows_about_sticks`):** `rekordbox_write.py`,
`ui/rekordbox_editor.py` and `RekordboxCueCache.database_for` (what the editor writes to). A memory cue edited on a stick's track stays in
Dubcrate's own table (it is never queued for a write); it still shows in the player and goes to the card.

**Measured on the owner's stick (read-only):** a track loaded the way the player loads it (`load_media(path, stick root, analysis path)`) gave its
beat grid (929 beats), 26 phrases and the 47,730-column detail waveform; its cues came through `RekordboxCues` in audio time (this file's lead is
25 ms, so hot A at 0.027 s became 0.002 s).

## Step 3: the window (2026-11-24)

**File > Import rekordbox USB** is a submenu filled each time it opens (`MainWindow._fill_usb_menu`): one entry per plugged-in stick
(`find_sticks(mounted_volumes())`, named for the drive; the tooltip says "read again" for one already imported), "No rekordbox USB stick found" when
there is none, and **Choose Folder…** (`import_usb()` with no path: a folder dialog starting in `/Volumes` where there is one). The root, its
`PIONEER` folder or the database all do. A folder that is no stick gets a plain box on the GUI thread (`rekordbox_usb.problem`), not a failure on the
worker. It reads on a worker with Cancel, like any import (`start_import("rekordbox_usb", root)`), and waits with the other import actions while something
is being read. Tests: `tests/test_ui_window.py` (the menu, the choice, the cancel, the explanation, busy); gallery scenes `menus-file-usb`
and `msg-usb-not-a-stick` (the main window's `mounted_volumes` is stubbed in every test and in the gallery, so a real drive is never listed there).

## The leftovers (2026-11-24)

- **An unplugged stick** (`MainWindow.check_usb_sticks`, every `USB_POLL_MS` = 2 s, plus once at start-up): one look at each stick's `exportLibrary.db`
  (`rekordbox_usb.stick_present`); when a stick has gone or come back its tracks' files are checked on a worker (`missing_tracks`: all of them when
  the stick is gone, no file looked at; else `os.path.exists` each) and `Library.set_missing` marks them, then the track list, the open disc and the
  discs list are refreshed. The tracks, cues, playlists and discs stay while the stick is away; the rows are greyed with "File not found", a burn is
  blocked with that reason, the player says so instead of failing in the decoder. The status bar says "“Name” was unplugged..." / "is plugged in
  again" (nothing on the first look at start-up). **It does not re-read the stick on plug-in**: its tracks come back from the stored copy; Re-import (or
  File > Import rekordbox USB) reads it again. Master.db sources on an unplugged drive still have the old behaviour (flags only change at an import).
- **A stick that comes back under another name** (`Martin` -> `Martin 1`): `rekordbox_usb.store_stick` (used by the window and `import-usb`) first
  looks for a stick source whose drive is not there now (`find_moved_source`) and where at least half of the smaller track list is the same
  (content id, path from the stick's root): such a stick is the same one, because a stick has no id of its own (`deviceName` is the drive's name, `masterDbId`
  the computer's). `Library.move_source` then re-points the source and every track's path **and re-keys what is kept by a file's path** (memory cue and loop
  edits, the preview cache: `UPDATE OR IGNORE`), so track ids, discs and edits survive; the source keeps its name unless one is given, and the import notes say
  it was moved. A stick whose drive is still plugged in is never taken (a copy of a stick is its own source).
- **`export.pdb`-only sticks stay refused** (decision 2). Not built, and not planned unless a stick that needs it turns up.
- Tests: `tests/test_library_rekordbox_usb.py` (moved sticks, edits following the file, the drive-still-there and different-stick cases, `set_missing`,
  `missing_tracks`, the command line) and `tests/test_ui_window.py` (unplug and replug, a stick missing at start-up). **Tried in tests only**: I could not
  unplug the owner's stick from here.

## Not done / open

- **Looked at in tests, a script and the offscreen gallery only, not in the running window:** the menu, the player, the track list's flags and Sync with a stick's tracks.
- Never tried: a stick with a different rekordbox version, a Windows/FAT32/exFAT stick, a stick with only `export.pdb`.
- Tracks on a stick are copies of files that are also in the computer's library; they are not matched as twins (different paths).
