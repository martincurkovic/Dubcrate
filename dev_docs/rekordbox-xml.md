# Reading a rekordbox XML file (2026-11-24)

The "rekordbox XML as an import path with no SQLCipher dependency" item. **Read-only; nothing that writes to rekordbox ever acts on it.**

## What it is

`library/rekordbox_xml.py` reads the `DJ_PLAYLISTS` file rekordbox exports (File > Export Collection in xml format; rekordbox 5, 6 and 7, and other programs
write the same format, and so does Dubcrate's own `playlist_export.write_xml`). It is parsed with the **standard library** (`xml.etree`), not
`pyrekordbox.rbxml`, which drops a path's leading slash and has no way to the cue marks. No database key, no SQLCipher.

- `read_xml(path, progress, cancel)` -> `RekordboxLibrary` (its `source` is the file), `read_xml_cues(path)` -> `{TrackID: [Cue]}`, `problem(path)` -> why a file
  is not one ("" if its first tag is `DJ_PLAYLISTS`; only that tag is read), `location_path(url)`.
- **Tracks** (`COLLECTION` > `TRACK`): Name, Artist, Album, Genre, Year, AverageBpm, Tonality (the key), TotalTime (seconds), BitRate, SampleRate, Comments,
  Rating (0 to 255 in steps of 51: one star each), Size, Label, Remixer, Composer, PlayCount, DateAdded. A track with no `Location` or `TrackID` is skipped, and so is a repeated
  `TrackID` (said in a note). `Location` is `file://localhost/Users/me/a%20b.mp3` (a Windows one `file://localhost/C:/Music/a.mp3`): decoded to a path, a drive letter without
  the slash in front of it, and a value that is not a URL taken as the path it is. `analysis_path` is None.
- **Playlists** (`PLAYLISTS` > `NODE`): `Type` 0 folders and 1 playlists, in file order, tracks by `TrackID` (`KeyType` 1 names them by location); the top `ROOT` node is not a folder
  of its own; an id that is not in the collection is left out; a node's `ext_id` is its place in the tree (`/0/2`), since two playlists can share a name.
- **Cues** (`POSITION_MARK`): `Num` -1 is a memory cue and 0 up a hot cue (0 = A); `Type` 4 is a loop (a hot cue saved as a loop keeps its slot); fades (1, 2) and "load" points (3), a mark with
  no `Start` and a loop whose end is not after its start are not cues. Times are rekordbox's, in seconds: the generic `cue_timing.to_audio` lead applies to them like any rekordbox cue.
- **Not read:** `TEMPO` (the beat grid). An XML file has no analysis files, so a track from it has no rekordbox waveform, phrases or beat grid; the player draws the waveform from
  the audio and Q stays off (it needs a beat grid). Turning TEMPO into one is possible and not done.

## In the library

Its own source kind, **`rekordbox_xml`** (schema v21; `_allow_source_kind` is the shared helper that relaxes the `source.kind` check by editing its stored definition, as v20 did for sticks). The
source's path is the XML file, its name the file's name. Everything that reads rekordbox's cues goes through `library/rekordbox_sources.py` (`KINDS`, `read_cues_at` by the `.xml` suffix), so the player, cue cache,
Sync, Import Cues and the finder read them, and `Library.rekordbox_twin` counts an XML track, so a file browsed in the Explorer at that path takes its BPM, key and cues. **The writers
(`rekordbox_write`, `RekordboxEditor`, `RekordboxCueCache.database_for`) stay `kind == "rekordbox"` only** (a test checks). Start-up does not re-read an XML source (only a computer's `master.db`).

Window: **File > Import rekordbox XML…** (`MainWindow.import_xml`, a file dialog; a wrong file gets a plain box on the GUI thread) and Re-import. CLI: `dubcrate import-xml PATH [--name N]`.

## Tried

- `tests/test_library_rekordbox_xml.py` over a hand-made rekordbox-style file (memory and hot cues, loops, fades, a Windows path, KeyType 1, duplicates), and a **round trip of Dubcrate's own export**
  (`playlist_export.write_xml` -> `read_xml`: same paths and playlists). `tests/test_ui_window.py` for the menu item.
- On the owner's own `~/Desktop/Dubcrate Playlists.xml` (read-only): 401 tracks and 26 nodes, files present. **That file has no cues** (Dubcrate writes none), so the cue reading is only tested on the
  hand-made file: **never tried on a file rekordbox itself wrote.**

## Not done

- A beat grid from `TEMPO`; a re-read when the file changes; Serato/Traktor/Engine files (other formats).
