# Changelog

What changed in each version. The notes of a GitHub release are this file's entry for that version.

## [0.1.0] - not released yet

The first release: plan and burn CDs for the CDJ-1000MK3 and other CD players, and put your cues on its SD card. Runs on macOS, Windows and Linux.

- **Bring in your music.** Read your rekordbox library, a rekordbox USB stick or XML file, or any folder. Dubcrate only reads them. Browse folders and drives in the Explorer without importing.
- **Plan discs like playlists.** Audio CDs (74 or 80 minutes, with an exact size bar) and MP3 CDs with folders. Even out the loudness of a disc's tracks if you like.
- **Burn.** Audio CDs with CD-TEXT, gapless, or MP3 CDs. Export a disc as files for any other burning program.
- **Preview.** A smooth waveform with rekordbox's beat grid, phrases and cues. Scratch, loop, beat jump, and add or delete memory cues (saved into rekordbox if you allow it).
- **Sync the SD card.** Put your rekordbox cues and each track's waveform on the CDJ-1000MK3's card, so a disc loads with them ready. Import back what you set on the player, and format a blank card.
- **Labels and the gig.** Print index labels for the case, and export a one-page phone search that tells you which disc and track, with no signal needed.
- Four looks (System, Light, Dark, Macintosh System 6), and a `dubcrate` command-line tool.

### Good to know

- Tried with a CDJ-1000MK3 (firmware 2.20) and rekordbox 7. Other players and rekordbox 6 haven't been checked.
- Not yet tried on real gear: MP3 CDs burned from a Mac, importing cues set on the player, loudness matching on a player, saving memory cues into rekordbox from inside the app (the command line has been), and printing on paper.
- The app isn't signed, so macOS and Windows warn you the first time (see the README). On Windows, burning needs an administrator account.
- An update check runs once a day and can be switched off. It's the only time Dubcrate uses the network.
