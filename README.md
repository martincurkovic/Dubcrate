# Dubcrate

[![Build](https://github.com/martincurkovic/Dubcrate/actions/workflows/build.yml/badge.svg)](https://github.com/martincurkovic/Dubcrate/actions/workflows/build.yml)
[![Tests](https://github.com/martincurkovic/Dubcrate/actions/workflows/test.yml/badge.svg)](https://github.com/martincurkovic/Dubcrate/actions/workflows/test.yml)
[![Downloads](https://img.shields.io/github/downloads/martincurkovic/Dubcrate/total?label=downloads)](https://github.com/martincurkovic/Dubcrate/releases)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Qt 6.12 LTS](https://img.shields.io/badge/Qt-6.12%20LTS-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey)](#what-you-need)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

**Burn audio CDs for old-style CDJs from your DJ library.**

Older players like the CDJ-1000MK3 play audio CDs but can't read a rekordbox USB
stick. Dubcrate lets you pick tracks from your rekordbox library (or any folder of
music), plan CDs that fit exactly, preview them with waveforms, and burn audio CDs
whose track titles show on the player's display.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="src/dubcrate/ui/help/screenshots/main-window-dark.png">
    <img alt="Dubcrate with a rekordbox playlist open: waveforms and phrases on top, the Library on the left, the track list in the middle and the discs on the right" src="src/dubcrate/ui/help/screenshots/main-window-light.png" width="860">
  </picture>
</p>

> **Status: early development, not released yet.** You can already import, preview
> and plan discs in the app. The app's Burn window is new and has not been tried on a
> real drive yet; burning from a command line is the route that has been proven (see
> [dev_docs/burning.md](dev_docs/burning.md)).
> Pre-built downloads (a macOS `.dmg`) will appear on the
> [Releases page](https://github.com/martincurkovic/Dubcrate/releases) once there is a
> release; until then, [CONTRIBUTING.md](CONTRIBUTING.md) explains how to run it from
> source and [BUILDING.md](BUILDING.md) how to build the app yourself.

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

<table>
  <tr>
    <td align="center"><img alt="An audio disc open: its tracks as they will be written, and the capacity bar showing how full it is" src="src/dubcrate/ui/help/screenshots/disc-open.png" width="420"><br><sub>A disc and its capacity bar</sub></td>
    <td align="center"><img alt="An MP3 CD with folders, listed under the disc on the right" src="src/dubcrate/ui/help/screenshots/mp3-disc.png" width="420"><br><sub>MP3 CDs, with folders</sub></td>
  </tr>
  <tr>
    <td align="center"><img alt="The Explorer tab browsing a folder of music, with a pinned folder's tab below it" src="src/dubcrate/ui/help/screenshots/explorer.png" width="420"><br><sub>Explorer and pinned folders</sub></td>
    <td align="center"><img alt="The label window: two discs' track lists laid out on one sheet of paper" src="src/dubcrate/ui/help/screenshots/labels.png" width="420"><br><sub>Index labels for the case</sub></td>
  </tr>
  <tr>
    <td align="center"><img alt="The burn window: the drive, the burn speed and the blank CD-R confirmation" src="src/dubcrate/ui/help/screenshots/burn.png" width="420"><br><sub>The burn window</sub></td>
    <td align="center"><img alt="The Dubcrate window in the Macintosh System 6 theme: black and white with a bitmap-style font" src="src/dubcrate/ui/help/screenshots/main-window-system6.png" width="420"><br><sub>Macintosh System 6 theme</sub></td>
  </tr>
</table>

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

## Download & installation

Download the latest `.dmg` from the [Releases page](https://github.com/martincurkovic/Dubcrate/releases)
(`Dubcrate-macos-arm.dmg` for Apple Silicon, `Dubcrate-macos-intel.dmg` for Intel Macs),
open it and drag Dubcrate to your Applications folder. It needs macOS 14 or later, and
`ffmpeg` (see above). Dubcrate checks GitHub for a newer version when it starts, at most
once a day (switch that off in *Settings ▸ Library*; *Help ▸ Check for Updates…* checks on
request). That is the only time it uses the network.

The app is not signed, because a signing certificate is hard to justify for a free
single-person project, so macOS will warn about it the first time. To open it anyway:

1. Try to open Dubcrate normally.
2. Open System Settings ▸ Privacy & Security and scroll down.
3. Click **Open Anyway**, and confirm with your password or Touch ID.

If you would rather not trust a download, [BUILDING.md](BUILDING.md) builds it from source.

## Using Dubcrate

1. **Import your music.** *File ▸ Import rekordbox Library…* reads your rekordbox
   library (it only reads a temporary copy, so rekordbox is never changed), or
   *File ▸ Import Folder…* reads a folder of music. Imported sources appear on the
   left (the *Library* tab), with their playlists and folders beneath them. A rekordbox library you have
   imported is brought up to date by itself each time Dubcrate starts (while a splash
   screen shows); switch that off in *Settings ▸ Library*. The *Explorer* tab beside it
   browses the folders on your Mac, or a plugged-in drive, without importing anything:
   click a folder and its audio files appear, ready to preview and add to a disc.
2. **Browse and listen.** Pick a playlist, search by title, artist, album or genre,
   and sort by clicking a column heading. Double-click a track to preview it.
   Right-click the column headings to show or hide columns: only the essentials show at
   first, and *More Columns* adds year, star rating, My Tags, label and more from rekordbox.
3. **Build a disc.** Your discs are playlists, listed on the right and filed in folders
   like rekordbox playlists. *New Disc* makes one; click it to open its tracks in the
   middle, then drag tracks onto it (or onto any disc in the list), or select them and
   press Return. Drag rows to reorder, and press Delete to take a track off.
   **Fit All to Disc** adds tracks in order until the disc is full, and
   *Disc ▸ Split Shown Tracks Across Discs…* turns a long list into several discs.
4. **Check the bar.** It shows each track as a coloured block. A disc can't be burned
   if it is over its size or contains a track whose file can't be found. (A CD holds
   at most 99 tracks, and Dubcrate stops adding at that point.)
5. **Burn it.** *Burn…* under the disc (or *Disc ▸ Burn Disc…*) shows the drive and
   what the blank disc inside holds, and asks you to confirm that a blank CD-R is
   loaded. The burn speed starts at your default from *Settings* and can be changed
   for one burn. A burned disc is just a disc: it stays editable, remembers when it was
   burned, and *Duplicate* (right-click) keeps a copy of a mix before you change it.
6. **Print a label for the case.** *Disc ▸ Print Labels…* (⌘P) lays out the track list of
   one or several discs, audio or MP3 (with an MP3 disc's folders), on sticker paper:
   choose the paper (A3 to A6, Letter, Legal), the label size, how many labels to spread a
   long list over, columns, font size, and whether to show BPM, key and duration. Several
   discs share a page so little paper is wasted. Preview it (zoom, pinch on a trackpad),
   then print it or save a PDF. Number boxes in the app can be dragged up and down like a knob.

In the overview, click to jump to a place in the track. In the zoomed waveform, the
mouse wheel zooms in and out, and dragging it scratches the track as on a CDJ's jog
wheel. The keyboard shortcuts, and everything else in the app, are in *Help ▸ User Guide*.

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
- **"ffmpeg not found."** Install it (`brew install ffmpeg`) and restart Dubcrate. The
  downloadable app finds Homebrew's copy in `/opt/homebrew/bin` and `/usr/local/bin`.
- **Dubcrate can't find my rekordbox library.** Choose *Choose File…* when importing
  and pick `master.db`, normally in `~/Library/Pioneer/rekordbox/`.
- **A track is greyed out, or its row says the file is missing.** The file has moved
  or its drive is not connected. Reconnect it, then re-import the source (⌘R).
- **A disc says it is over its size.** The capacity bar shows where the disc ends.
  Take tracks off, switch to a longer disc size, or split the playlist.

## What's coming

Folders you can pin as tabs of their own, other DJ libraries (Mixxx first), and Windows
and Linux support.

## License

Dubcrate is free software, released under the
[GNU General Public License, version 3 or (at your option) any later version](LICENSE).
The licences of what it includes are listed under *Help ▸ Third-Party Notices…*
([the same text](src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md)).

The Macintosh System 6 theme uses the ChiKareGo2 font by Giles Booth (a recreation
of Chicago 12), licensed [CC BY](src/dubcrate/ui/fonts/README.txt). Dubcrate is not
affiliated with Apple, AlphaTheta or Pioneer DJ. rekordbox, CDJ and Pioneer DJ are
trademarks of their owners.
