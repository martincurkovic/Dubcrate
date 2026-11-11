# Dubcrate User Guide

Dubcrate helps you put music from your rekordbox library (or any folder) onto audio CDs for older players such as the CDJ-1000MK3. You pick tracks, plan discs that fit exactly, preview them with waveforms, and burn CDs whose track titles show on the player's display.

> **Burning from the window is new.** It has been tried with a stand-in burner but not yet on a real drive. The command line is the route that has been proven: see [Burning a disc](#burning-a-disc).

---

## Contents

- [Getting started](#getting-started)
- [The main window](#the-main-window)
- [Importing your music](#importing-your-music)
- [Browsing and searching](#browsing-and-searching)
- [Listening to tracks](#listening-to-tracks)
- [Planning a disc](#planning-a-disc)
- [Checking tracks you already used](#checking-tracks-you-already-used)
- [Discs you have burned](#discs-you-have-burned)
- [Burning a disc](#burning-a-disc)
- [Settings](#settings)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Troubleshooting](#troubleshooting)
- [Good to know](#good-to-know)

---

## Getting started

1. **Import your music.** Choose **File > Import Folder…** for a folder of music, or **File > Import rekordbox Library…** for your rekordbox library.
2. **Find some tracks.** Pick a playlist or folder on the left, or search.
3. **Build a disc.** Drag tracks onto the disc on the right, or select them and press **Return**.
4. **Watch the bar.** It shows how full the disc is and turns red if the disc is too long.
5. **Burn it.** Press **Burn…** under the disc, check the drive, tick that a blank CD-R is inside, and burn.

You need **ffmpeg** installed: Dubcrate uses it to read tracks and to prepare them for the disc. With Homebrew: `brew install ffmpeg`.

---

## The main window

- **Sources** (left): everything you have imported, with its playlists and folders beneath it.
- **Tracks** (middle): the tracks of whatever is selected on the left, with a search box above, and the buttons **Add to Disc** and **Fit All to Disc** below.
- **Player** (across the top): play and pause, previous and next, the title, time, key and BPM of the track you are previewing, a volume control, and two waveforms.
- **Disc** (right): the disc you are planning, or the discs you have already burned. See [Planning a disc](#planning-a-disc) and [Discs you have burned](#discs-you-have-burned).

**View > Theme** switches between System, Light, Dark and Macintosh System 6 (black and white, with a bitmap-style font).

**Help > User Guide** opens this guide in its own window, which can stay open beside the app. Press **⌘F** in it to search the page, and click the links in the contents to jump to a section.

---

## Importing your music

| Do this | To |
| --- | --- |
| **File > Import Folder…** (⌘O) | Read a folder of music. Each sub-folder becomes something you can browse. |
| **File > Import rekordbox Library…** (⇧⌘O) | Read your rekordbox library, with its folders and playlists in their running order. |
| **File > Re-import Selected Source** (⌘R) | Bring the selected source up to date after you added or moved music. |
| **File > Remove Selected Source…** | Forget a source. Your music files are not touched. |

**Your music and your rekordbox library are never changed.** Dubcrate reads a temporary copy of the rekordbox database and only reads your audio files. It keeps its own library of imported track information and your discs.

If Dubcrate can't find your rekordbox library, choose **Choose File…** when it asks and pick `master.db`, normally in `~/Library/Pioneer/rekordbox/`. Version 7 is the version Dubcrate has been tried with.

Re-importing keeps your discs intact. A track that has vanished from its source but is on a disc is kept and marked, so a disc never silently loses a track.

Supported music: MP3, WAV, FLAC, AIFF, AAC/M4A, Ogg, Opus and WMA.

---

## Browsing and searching

- Click a source, playlist or folder on the left to list its tracks.
- Type in the **search box** to filter by title, artist, album or genre.
- Click a **column heading** to sort by it, and click again to reverse.
- **Right-click a column heading** to show or hide columns.
- A **●** at the start of a row shows that the track is already on the disc you are planning.

Tracks whose file can't be found are greyed out in the list, and shown in red on the disc. The file has moved, or the drive it lives on isn't connected. Reconnect it and re-import the source.

---

## Listening to tracks

**Double-click** a track to preview it, and press **Space** to play or pause. The **Preview** column shows the whole track as a waveform: low frequencies in blue, mids in amber and highs in white.

The two waveforms in the player:

- The **overview** shows the whole track. For rekordbox tracks it also shows the phrases (such as INTRO, CHORUS, DOWN, UP and OUTRO) and any cue points. Click anywhere in it to jump there.
- The **detail view** is zoomed in and scrolls past a fixed playhead, with the beat grid drawn on it. Click to jump, and use the mouse wheel to zoom.

For rekordbox tracks the waveforms, beat grid, phrases and cues come from rekordbox's own analysis. For other music Dubcrate draws the waveform itself. Beat grids and cues are only shown for your planning: **they do not go on the CD.**

Choose where previews play, and test it, in [Settings](#settings).

---

## Planning a disc

The **Planned** view on the right shows the disc you are working on. Use the list at its top to switch discs, **New** to start another, and **⋯** to rename or delete one.

**Adding tracks**

- Drag tracks from the track list onto the disc, **or** select them and press **Return**, **⌘D** or **Add to Disc**.
- **Fit All to Disc** adds every track shown, in order, until the disc is full.
- **Disc > Split Shown Tracks Across Discs…** (⇧⌘D) cuts a long list into as many discs as it takes, keeping the order.
- If you add tracks and no disc exists yet, one is created for you.

**Arranging**

- Drag rows up and down to reorder them.
- Select rows and press **Delete** (or click **Remove**) to take them off the disc.

**Size and gaps**

New discs are planned for **80 minutes** and are **gapless**: no silence between songs, so the most music fits. Change either under **File > Settings… > Discs** (see [Settings](#settings)). The new choice applies to discs you create from then on; a disc keeps the size and gaps it was made with.

The disc size you plan for is only a guide. When you burn, **the disc in the drive is what counts**: see [Burning a disc](#burning-a-disc).

**The capacity bar**

The bar under the list shows each track as a coloured block, with the time used and the time left. When a disc is over its size, the bar still shows everything and marks where the disc would end. A CD holds at most **99 tracks**, and Dubcrate stops adding at that point.

A length shown with **~** is an estimate. Dubcrate measures each track's exact length when you add it and again when the disc is prepared, and the prepared length is the one that counts.

A disc can't be burned if it is empty, over its size, or holds a track whose file is missing or no longer in its source. When it's ready, press **Burn…**: see [Burning a disc](#burning-a-disc).

---

## Checking tracks you already used

When you add a track that is **already on this disc, planned on another disc, or burned on a previous disc**, Dubcrate asks before adding it:

> 2 of the 5 tracks are already used:
>
> • Artist - Title: already on this disc; burned on "Friday night" (2026-11-01)
>
> Add anyway?

Choose **Yes** to add everything, or **No** to cancel and add nothing. It names each place the track is used, and lists the first eight tracks if there are more.

Only discs that were really burned count. A test write (a simulated burn) puts nothing on a disc, so it doesn't trigger this message.

---

## Discs you have burned

Every real burn is saved as a record. Switch the right-hand panel from **Planned** to **Burned** to see them, newest first. Each record shows what went on the disc: the tracks in order, their times, and when, on which drive and at what speed it was burned.

- A record is a **frozen snapshot**. Editing or deleting the planned disc afterwards never changes it, and neither does removing the music source.
- Tracks that are no longer in your library are dimmed. They were burned, but can't be put on another disc.
- **Duplicate** makes a new, editable disc from the record, which is handy for re-burning or for tweaking a copy. Tracks no longer in your library are left out, and Dubcrate tells you how many.
- **Delete** forgets the record. The CD itself is unaffected, but Dubcrate will stop warning you about its tracks.

Your planned discs stay editable after a burn. The record is a separate copy of what was burned.

---

## Burning a disc

> **A real burn uses up a blank CD-R.** Use a blank **CD-R**, not a CD-RW, which old players read badly.

**From the window**

1. Plan the disc, then press **Burn…** under it (or choose **Disc > Burn Disc…**, ⌘B).
2. Put a blank CD-R in the drive. Dubcrate asks the drive what is inside and shows it: the drive's name, whether the disc looks blank, and how much it holds. Press **Check Drive** after changing the disc.
3. Check the **Speed**. It starts at your default from **File > Settings… > Burning**; change it here to use another speed for this burn only.
4. Tick **A blank CD-R is in the drive**. The **Burn** button stays grey until you do, and Return never starts a burn.
5. Press **Burn**. The log shows each track being prepared and then the burn. The window can't be closed until it finishes.

Things to know:

- Dubcrate **won't burn** if there is no disc, if the music is longer than what the disc holds (a 74-minute disc with 78 minutes planned, say), or if the disc can't be burned as planned. The reasons are listed in red. If the drive can't say how much a disc holds, Dubcrate goes by the size the disc was planned for.
- **Test only** runs everything except writing to the disc (the laser stays off), which is a safe way to check your drive. A test isn't saved under **Burned**.
- After a real burn the disc is saved under **Burned** and the window shows it. Ticking the box is needed again before every burn.
- A failed burn says why in the log, and nothing is saved. A burn can't be stopped once it has started.

**From the command line**

The command line does the same job. Put the disc number in place of `1` (the number is shown by `dubcrate disc list`).

```
dubcrate burn 1                      # a dry run: checks the disc and burns nothing
dubcrate burn 1 --yes --simulate     # a laser-off test write, with a disc in the drive
dubcrate burn 1 --yes                # really burns
```

- The dry run lists anything that stops the disc being burned.
- Before burning for real, Dubcrate asks the drive about the disc inside. It says how much the disc holds, and **refuses to start if the music won't fit** (for example, a 74-minute disc with 78 minutes planned). It also stops if the drive says there is no disc. If the drive can't tell, Dubcrate goes by the size the disc was planned for.
- **`--speed`** sets the burn speed. The default is 10x. **Burn slowly:** old players read discs burned at a low speed more reliably.
- Every track is converted to CD quality (16-bit, 44.1 kHz stereo) and the disc gets **CD-TEXT** titles, so the artist and title show on the player.
- After a successful real burn, Dubcrate saves the record shown under **Burned**.

The full list of command-line options is in the project's developer notes.

---

## Settings

**File > Settings…** (⌘,) has four tabs:

- **Discs:** how **new** discs are planned: **Plan new discs for** 80 or 74 minutes, and **Between tracks**, which is **Gapless** unless you choose 1, 2 or 3 seconds of silence.
- **Burning:** the **Burn speed** a burn starts with (10x unless you change it). Old players read discs burned slowly more reliably, and 10x to 16x is a good range. Your drive may not offer every speed. You can still pick another speed for a single burn in the burn window.
- **Audio:** the **Output device** for previews and the **Buffer size**, with a button that plays a short test tone.
- **Appearance:** the **Theme**, the same choice as **View > Theme**.

---

## Keyboard shortcuts

| Shortcut | Does |
| --- | --- |
| Double-click a track | Preview it |
| Space | Play or pause |
| Return, or ⌘D | Add the selected tracks to the disc |
| Delete | Take the selected tracks off the disc |
| ⌘O | Import a folder |
| ⇧⌘O | Import a rekordbox library |
| ⌘R | Re-import the selected source |
| ⌘N | New disc |
| ⌘B | Burn the disc |
| ⇧⌘D | Split the shown tracks across discs |
| ⌘, | Settings |
| ⌘F (in the guide) | Find in the guide |

---

## Troubleshooting

- **A preview crackles.** Raise the buffer size in **Settings** (try 2048 frames).
- **No sound, or the wrong output.** Choose the output device in **Settings** and press the test tone button.
- **"ffmpeg not found".** Install it (`brew install ffmpeg`) and restart Dubcrate.
- **Dubcrate can't find my rekordbox library.** Choose **Choose File…** when importing and pick `master.db`.
- **A track is greyed out, or its file is missing.** Reconnect the drive or put the file back, then re-import the source (⌘R).
- **The Burn button is grey.** Read the red lines in the burn window: the disc may be empty, too long for the disc in the drive, or missing a file. Also tick **A blank CD-R is in the drive**, and press **Check Drive** if you just changed the disc.
- **A disc is over its size.** Take tracks off, switch to a longer disc size, or split the playlist.
- **A track says it is no longer in its source.** It disappeared from the source at the last re-import but is kept because a disc uses it. Take it off the disc before burning.

---

## Good to know

- **Titles use CD-TEXT**, which handles Western European characters. Other characters are simplified, or become a question mark, on the disc.
- **The disc holds only audio and titles.** Cue points, beat grids and rekordbox analysis can't go on an audio CD.
- **Everything is converted to CD quality** when burned, so a high-resolution file becomes 16-bit, 44.1 kHz.
- Dubcrate keeps its library in `~/Library/Application Support/Dubcrate/`.
- Dubcrate is free software under the GNU General Public License, version 3 or later. It is not affiliated with Apple, AlphaTheta or Pioneer DJ. rekordbox, CDJ and Pioneer DJ are trademarks of their owners.
