# Changelog

What changed in each version. The notes of a GitHub release are this file's entry for that version.

## [0.1.0] - not released yet

The first release, for the CDJ-1000MK3 and other players that read CDs. It runs on macOS, Windows and Linux.

### Your music

- Read your **rekordbox 7 library**, a **rekordbox USB stick** or a **rekordbox XML file**, or any **folder** of music. Dubcrate only reads them.
- Browse folders and drives in the **Explorer** without importing, and pin the ones you use.
- A USB stick you unplug stays in the library, greyed out, and comes back when you plug it in.

### Discs and burning

- Plan **audio CDs** (74 or 80 minutes, with an exact size bar) and **MP3 CDs** with folders. Discs are playlists you keep in folders.
- **Burn** audio CDs with CD-TEXT titles, gapless by default. Tried on a real drive on Mac, Windows and Linux; the discs play on a CDJ-1000MK3.
- MP3 CDs have been burned and played from Windows and Linux. Not yet tried from a Mac.
- Even out the loudness of a disc's tracks. *Not yet listened to on a CDJ.*
- Export a disc as files for any other burning program.
- On Windows, burning needs an administrator account.

### Preview and cues

- A smooth waveform with rekordbox's beat grid, phrases and cue points. Scratch, beat jump, loops and quantise work too.
- Add and delete **memory cues and loops** in the player. If you allow it, Dubcrate saves them into your rekordbox library.
  *The command-line version has been tried on a real library. The in-app version has not, so keep a backup.*

### The CDJ-1000MK3's SD card

- **Sync** puts your rekordbox cues and each track's waveform on the card, so a disc loads with them ready.
  Tried on a CDJ-1000MK3, for audio and MP3 discs. Always eject the card before pulling it.
- **Import Cues** brings back what you set on the player. *Not yet tried with cues set on a real player.*
- **Format a Card** sets up a blank SD card (2 GB or smaller). Tried on Linux and Windows, not yet on a Mac.

### Labels and the gig

- **Print index labels** for the case, to a printer or a PDF. *Only checked by saving PDFs so far.*
- **Export Track Finder** makes one web page for your phone. Type part of a title and it tells you which disc and track.
  It works with no signal. You can star tracks, group them by disc and share the list as text. The search was used at a gig on an iPhone.
- **Export Playlists** saves your discs as rekordbox XML or M3U files.

### Also

- Four looks: System, Light, Dark and Macintosh System 6.
- An update check once a day, which you can turn off. It's the only time Dubcrate uses the network.
- A `dubcrate` command-line tool.

### Known limits

- The app isn't signed, so macOS and Windows warn you the first time (see the README).
- Written for the CDJ-1000MK3's SD card (firmware 2.20). Other players haven't been looked at.
- rekordbox 7 is what it has been tried with. Version 6 should work but is untested.
- Windows 10 hasn't been tried.
