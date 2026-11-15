# Changelog

All notable changes to Dubcrate. The in-app update check reads the notes of each GitHub release, so
the text of a release is this file's entry for that version (pasted into the release by hand).

**This is a draft for the first release, written from the git history (2026-11-15): edit it to say what
you want people to read.** Where a feature has been tried on real hardware it says so; where it has only
been tested by the automated tests it says that too.

## [0.1.0] - not released yet

The first release: macOS (Apple Silicon and Intel) and, new, Linux (an AppImage), for the CDJ-1000MK3 and other players that read audio CDs.

### Library and discs

- Import a **rekordbox 7** library (read-only: a copy of its database is read) with its folders and playlists, or any
  **folder of music**. Re-importing keeps your discs intact; a track that has left its source but is on a disc is kept and marked.
- The **Explorer** browses folders and drives without importing, with **pinned folders** as tabs of their own.
- **Discs are playlists** you keep in folders, with an exact capacity bar for 74 or 80 minute discs; **Fit All to Disc**,
  **Split Shown Tracks Across Discs**, drag and drop, and a warning when a disc is over its size.
- Track text for the disc (title and artist) can be edited for a disc without touching the library, rekordbox or the file.

### Burning

- **Audio CDs with CD-TEXT**, gapless by default, burned through macOS's own burning framework (a LiteOn USB burner
  was the one tried: track times and CD-TEXT, accents included, were right on a CDJ-1000MK3).
- **MP3 CDs**, with folders, constant bitrate, written as ISO 9660 + Joliet. *Tested by the automated tests; not yet tried on a real disc.*
- **Level matching** for audio CDs: bring tracks to a loudness (or peak-normalise them), with presets. *Tested; not yet listened to on a CDJ.*
- **Export** a disc as files for any other burning program (numbered WAVs and a table of contents), with no drive needed.
- A burn **checks the disc in the drive** first and, on a Mac, reads the burned disc's table of contents afterwards to say whether it is the disc that was planned. *The second check has not yet run on a real burn from the window.*

### Preview

- The player is laid out like rbxport's: controls down the left (previous / next, beat jump with a size menu, CUE and play), hot cue buttons A to H, memory cue buttons and loop controls under the waveform, zoom buttons over it, and **Memory / Hot Cue / Info** tabs on the right. Drag a track from a list onto the player to load it. The Info tab shows the track's **cover art** (from MP3, FLAC and M4A files; a placeholder when there is none). rekordbox's cues stay read-only. **CUE** (a temporary cue point, hold to preview), **automatic and manual loops** with RELOOP, and **quantise** work in the preview too, for finding your way round a track: they last until another track is loaded and are never saved. *Not yet used on a real rekordbox library.* **Memory cues can be added, moved and deleted in the player** (MEMORY, the list's ✕, right-click; Reset goes back): they are kept in Dubcrate, never written to rekordbox, and go to the card when you sync.
- A **player** with waveforms (rekordbox's own for rekordbox tracks, with beat grid, phrases and cues), scratching by dragging,
  beat jump with the arrow keys, play-through to the next track, and **four looks** (System, Light, Dark, Macintosh System 6).

### Index labels

- **Print labels** for the case: one or several discs on a sheet (A3 to A6, Letter, Legal), MP3 folders as headings,
  optional BPM, key and duration columns, a zoomable preview, to a printer or a PDF.

### The CDJ-1000MK3's SD card (new, and the part that has had the least testing)

- **Sync** writes your **rekordbox cues** to the card as memory cues, so a disc loads in the player with them already set. Hot cues
  and anything already on the card are never touched, and the card's files are copied aside before anything is written. *Tried: a
  CDJ-1000MK3 (firmware 2.20) showed every cue written this way.*
- **Sync also writes the player's waveform for each track**, so the player doesn't analyse a track when it loads. Dubcrate works the
  waveform out itself (from the low notes the player draws), usually while burning. *Tried: the player accepted, drew and kept
  waveforms written this way. The Sync tab's own waveform writing has not yet been tried on a card in a player.*
- **Format a Card** sets up a blank SD card from scratch (FAT16, the PIONEER folder), so a card can be prepared at home, on a Mac,
  Linux or Windows. *Not yet shown to a player, and the Linux and Windows ways have not been tried on a real card.* Only plain SD cards of 2 GB or less work in the player.
- The Sync tab: the card on the left, a **browser of the picked disc's tracks** in the middle (cues and waveform state for each, and the picked track's waveform drawn at the bottom as the player's display draws it, with an **Analyse Waveforms** button to work them out without writing to a card), and the **discs to tick** on the right, so a disc can be looked through without leaving Sync. **Format a Card…** and **Eject** are in the card list's right-click menu.

### Under the hood

- Imports, length measuring, waveform analysis and level measuring show their progress and can be **cancelled**; a folder track's waveform strip is remembered between sessions.
- A built-in **update check** (the only thing Dubcrate sends over the network: a request for GitHub's release list, once a day at
  start-up unless you switch it off), a **log file** (Help > Show Log File), and a **command-line tool** (`dubcrate`).
- ffmpeg and ffprobe are bundled (LGPL builds), so there is nothing to install.
- GPL-3.0-or-later.

### Known limits

- macOS is the system Dubcrate has been used on. **Linux** (an AppImage, x86_64) is built and opened by an automated test, but has not been run on a real desktop, a drive or a card; install `cdrdao` with your package manager to burn. Windows is not built yet. The rekordbox importer is tried with rekordbox 7, and version 6 is untested.
- The app is **not signed**, so macOS asks you to allow it the first time (see the README).
- Written for the CDJ-1000MK3's card (firmware 2.20); the card format of other players has not been looked at.
