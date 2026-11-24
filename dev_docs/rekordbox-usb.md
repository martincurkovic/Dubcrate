# Reading a rekordbox USB stick (2026-11-24)

Step 1 of the "read a rekordbox USB stick" item: the pure reader and the command line. **Read-only on the stick, always.**
Steps 2 and 3 (the preview, Sync and cue cache for a stick's tracks, then the window) are not built.

## The decisions (the owner, 2026-11-24: "defaults")

1. A stick is its own source kind, `rekordbox_usb` (schema v20). The code that WRITES to rekordbox (`rekordbox_write`,
   `ui/rekordbox_editor.py`) asks for `kind == "rekordbox"`, so it can never see a stick. Anything that reads a rekordbox
   source's cues, analysis or `master.db` path has to be given a per-kind reader before it can handle a stick (step 2).
2. `exportLibrary.db` only ("OneLibrary", rekordbox 7). A stick with only the older `export.pdb` is refused in words.
3. What is read: tracks (title, artist, album, genre, year, BPM, key, rating, label, remixer, composer, My Tags,
   play count, date added, comment), playlists and folders, and cues (memory, hot, loops), plus each track's `USBANLZ` path.
4. An unplugged stick: the start-up re-read only refreshes `kind == "rekordbox"`, so a stick is left alone (its files show as missing).
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

## Not done / open

- Step 2: `RekordboxCues`, `ui/cue_cache.py`, the preview's analysis root (`PreviewController._analysis_root`), the track list's preview column and
  `track_finder` need to read a stick's cues and analysis (a per-kind reader and root; `share_root(source.path)` is `master.db`'s).
- Step 3: the window's import, with detected sticks and Choose Folder…; a re-import when a stick is plugged in.
- Never tried: a stick with a different rekordbox version, a Windows/FAT32/exFAT stick, a stick with only `export.pdb`.
- Tracks on a stick are copies of files that are also in the computer's library; they are not matched as twins (different paths).
