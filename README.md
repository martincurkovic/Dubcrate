# Dubcrate

**Burn audio CDs for old-style CDJs from your DJ library.**

Older players like the CDJ-1000MK3 play audio CDs but can't read a rekordbox USB
stick. Dubcrate lets you pick tracks from your rekordbox library (or any folder of
music), plan CDs that fit exactly, preview them with waveforms, and burn audio CDs
whose track titles show on the player's display.

> **Status: early development, not released yet.** You can already import, preview
> and plan discs in the app. The app's Burn window is new and has not been tried on a
> real drive yet; burning from a command line is the route that has been proven (see
> [dev_docs/burning.md](dev_docs/burning.md)).
> Pre-built downloads will appear here once there is a release; until then,
> [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run it from source.

## What it does

- **Imports your rekordbox library** (read-only), with your folders and playlists
  in their running order, or **any folder of music**.
- **Previews tracks** with a full-track waveform and a zoomed waveform that scrolls
  past the playhead. For rekordbox tracks it shows rekordbox's own waveform, beat
  grid, phrases (INTRO, CHORUS, DOWN, UP, OUTRO) and any cue points; for other music it
  draws the waveform itself. Waveforms come in three looks, as in rekordbox: 3-band,
  RGB and blue, on a background that follows the theme.
- **Plans discs** of 74 or 80 minutes, with a capacity bar that shows every track
  and turns red when a disc is too long. It fills a disc in order, or splits a long
  playlist across as many discs as it takes. Drag tracks on, drag them around.
- **Burns audio CDs with CD-TEXT**, gapless by default, converting every track to
  CD quality (16-bit, 44.1 kHz). The disc list shows just what a CD holds (artist,
  track name and time), and you can edit the artist and track name for a disc without
  changing rekordbox or your files.
- **Keeps its own playlists.** Your discs live in Dubcrate and never touch rekordbox.

## What you need

- **A Mac.** Windows and Linux are planned.
- **A CD burner** that can write CD-R (an external USB one is fine) and blank
  **CD-R** discs. CD-RW is not recommended for old players.
- **[ffmpeg](https://ffmpeg.org/)**, which Dubcrate uses to read tracks and prepare
  them for the disc. On a Mac with Homebrew: `brew install ffmpeg`.
- **A player that reads audio CDs**, such as the CDJ-1000MK3 it was built for.
- Music as **MP3, WAV, FLAC, AIFF, AAC/M4A, Ogg, Opus or WMA**.
- A **rekordbox 7** library if you want to import one. Version 7 is what Dubcrate
  has been tried with; 6 uses the same format and may work, but is untested.

## Using Dubcrate

1. **Import your music.** *File ▸ Import rekordbox Library…* reads your rekordbox
   library (it only reads a temporary copy, so rekordbox is never changed), or
   *File ▸ Import Folder…* reads a folder of music. Imported sources appear on the
   left, with their playlists and folders beneath them.
2. **Browse and listen.** Pick a playlist, search by title, artist, album or genre,
   and sort by clicking a column heading. Double-click a track to preview it.
   Right-click the column headings to show or hide columns: only the essentials show at
   first, and *More Columns* adds year, star rating, My Tags, label and more from rekordbox.
3. **Build a disc.** Drag tracks onto the disc on the right, or select them and press
   Return. *Disc ▸ New Disc…* starts another. Drag rows to reorder, and press Delete
   to take a track off. **Fit All to Disc** adds tracks in order until the disc is
   full, and *Disc ▸ Split Shown Tracks Across Discs…* turns a long list into several
   discs.
4. **Check the bar.** It shows each track as a coloured block. A disc can't be burned
   if it is over its size or contains a track whose file can't be found. (A CD holds
   at most 99 tracks, and Dubcrate stops adding at that point.)
5. **Burn it.** *Burn…* under the disc (or *Disc ▸ Burn Disc…*) shows the drive and
   what the blank disc inside holds, and asks you to confirm that a blank CD-R is
   loaded. The burn speed starts at your default from *Settings* and can be changed
   for one burn. Each burn is kept under *Burned*, from where *Duplicate* makes an
   editable copy.

### Keyboard shortcuts

| | |
| --- | --- |
| Preview a track | Double-click |
| Play / pause | Space |
| Add the selected tracks to the disc | Return, or ⌘D |
| Beat jump back / forward (track list) | ← / → |
| Take the selected tracks off the disc | Delete |
| Import a folder / a rekordbox library | ⌘O / ⇧⌘O |
| Re-import the selected source | ⌘R |
| Search for a track | ⌘F |
| New disc | ⌘N |
| Burn the disc | ⌘B |
| Split the shown tracks across discs | ⇧⌘D |
| Settings | ⌘, |

In the overview, click to jump to a place in the track. In the zoomed waveform, the
mouse wheel zooms in and out, and dragging it scratches the track as on a CDJ's jog
wheel.

### Settings

*Settings* (⌘,), in the Dubcrate menu, has tabs for where previews play and how large the
audio buffer is (with a button that plays a test tone), the length new discs are planned
for (80 minutes) and the gap between tracks (gapless), and the burn speed (10x). The
*View ▸ Theme* menu switches between System, Light, Dark and a Macintosh System 6 look.

## Good to know

- **Your music and rekordbox are never modified.** Dubcrate only reads them. It keeps
  its own library (imported track information and your discs) in
  `~/Library/Application Support/Dubcrate/`.
- **Cue points, beat grids and analysis are not put on the CD.** An audio CD can't
  hold them and an old CDJ couldn't read them anyway. Dubcrate shows them so you can
  plan, but the disc holds only the audio and the titles.
- **Titles use CD-TEXT**, which handles Western European characters. Characters
  outside that set are simplified, or become a question mark, on the disc.
- **Discs are gapless** unless you choose a pause between tracks, so there is no
  two-second silence between songs.
- **Everything is converted to CD quality** when burned, so a high-resolution file
  becomes 16-bit, 44.1 kHz, with the dither a proper conversion needs.
- **Burn slowly.** Old players read discs burned at a low speed more reliably than
  fast ones.

## Troubleshooting

- **A preview crackles.** Raise the buffer size in Settings (try 2048 frames).
- **No sound, or the wrong output.** Choose the output device in Settings and press
  *Play Test Tone*.
- **"ffmpeg not found."** Install it (`brew install ffmpeg`) and restart Dubcrate.
- **Dubcrate can't find my rekordbox library.** Choose *Choose File…* when importing
  and pick `master.db`, normally in `~/Library/Pioneer/rekordbox/`.
- **A track is greyed out, or its row says the file is missing.** The file has moved
  or its drive is not connected. Reconnect it, then re-import the source (⌘R).
- **A disc says it is over its size.** The capacity bar shows where the disc ends.
  Take tracks off, switch to a longer disc size, or split the playlist.

## What's coming

A folder browser alongside your libraries, a printable track list for the disc sleeve,
and Windows and Linux support.

## License

Dubcrate is free software, released under the
[GNU General Public License, version 3 or (at your option) any later version](LICENSE).

The Macintosh System 6 theme uses the ChiKareGo2 font by Giles Booth (a recreation
of Chicago 12), licensed [CC BY](src/dubcrate/ui/fonts/README.txt). Dubcrate is not
affiliated with Apple, AlphaTheta or Pioneer DJ. rekordbox, CDJ and Pioneer DJ are
trademarks of their owners.
