# The `dubcrate` command-line tool

Almost everything the window does with the library can also be done from a terminal, burning and
exporting included (the window's **Burn…** and **Disc > Export Disc as Files…** do the same), which is handy for scripts
and for trying things (and the only way to try some, such as `doctor` and `card check-waveforms`). Run it with
`uv run dubcrate ...`.

Pass `--library PATH` before the command to use a different library file (handy for
trying things without touching your real one). By default the library is
`~/Library/Application Support/Dubcrate/library.db`.

## Importing

Both importers are read-only: rekordbox's `master.db` is copied to a temp directory
and the copy is read, and audio files are only probed.

```sh
dubcrate import-folder ~/Music/Crate     # directories become browsable nodes
dubcrate import-rekordbox                # the installed library, or --db PATH
```

Re-importing a source syncs it in place and keeps track ids. A track that has gone
from its source, but is on a disc, is kept and flagged instead of silently dropped.

## Looking around

```sh
dubcrate sources                                         # imported sources, with ids
dubcrate nodes 1                                         # a source's playlist/folder tree
dubcrate tracks --source 1 --search kettama --sort bpm --desc
dubcrate tracks --node 40                                # a playlist, in its running order
```

`tracks` takes `--source`, `--node`, `--search`, `--sort`
(title, artist, album, genre, bpm, key, duration, year, rating, path), `--desc` and
`--limit`.

## Planning discs

A disc is capped to a real CD: 74 or 80 minutes, at most 99 tracks. Track lengths are
measured with ffprobe when a track goes on a disc (`~` in `disc show` marks one not
measured yet), and the rendered audio is checked against the capacity again before
anything is burned.

```sh
dubcrate disc new "Warm Up night"             # 80 min and gapless; --minutes 74, --pregap 2
dubcrate disc new "Car USB" --mp3             # an MP3 CD (--bitrate 128|192|256|320, the highest constant rate; default 320)
dubcrate disc list
dubcrate disc show 1                          # tracks, times, capacity
dubcrate disc add 1 --node 40                 # a whole playlist, in order
dubcrate disc add 1 --node 44 --fit           # only what still fits (--strict stops at the first misfit)
dubcrate disc add 1 --track 1475 1473 --at 1  # specific tracks, at a position (1-based)
dubcrate disc mkdir 5 "UK Garage" --in 2      # a folder on MP3 disc 5, inside folder 2 (omit --in for the disc's root)
dubcrate disc add 5 --node 44 --dir 2         # ... and add to that folder (omit --dir for the root)
dubcrate disc move 1 3 1                      # move track 3 to position 1
dubcrate disc remove 1 2                      # remove track 2
dubcrate disc text 1 3 --title "Name" --artist "Artist"   # what is written on the CD for track 3 (empty restores the track's own)
dubcrate disc set 1 --name "New name" --minutes 74 --pregap 0
dubcrate disc split --node 44 --name "Pop"    # as many discs as it takes, order kept
dubcrate disc split --node 44 --name "Mix" --mp3 --bitrate 256   # ... as MP3 discs, by file size
dubcrate disc delete 1                        # the tracks stay in the library
```

## Exporting

```sh
dubcrate export 1 ~/Desktop     # a new folder "<disc name> export" inside ~/Desktop
```

For an audio disc: numbered ASCII-named WAVs, `disc.toc` (cdrdao, CD-TEXT, file names relative to
it) and `README.txt` with the cdrdao commands. For an MP3 disc: the disc's folder, and the README
beside it. Nothing is overwritten (a second export is `... export 2`), the disc is not marked as
burned, and no drive is needed. Exit status 2 if the disc can't be exported (as `burn`), 1 if the
export failed.

## Exporting playlists

```sh
dubcrate export-playlist 1 ~/Desktop/night.xml                # one disc as a rekordbox XML file (never overwrites)
dubcrate export-playlist 1 ~/Desktop/night.m3u8 --format m3u  # or an M3U file
dubcrate export-playlists ~/Desktop/discs.xml                 # every disc in one XML (disc folders become folders)
dubcrate export-playlists ~/Desktop --format m3u              # a NEW folder "Dubcrate Playlists" of .m3u8 files
```

The window's **Disc > Export Playlists** and the Discs list's right-click do the same. Nothing is read from or written to
rekordbox; the XML is for rekordbox's own "rekordbox xml" import. Exit status 1 if nothing could be exported.

## Track finder (which disc is this track on)

```sh
dubcrate export-finder ~/Desktop/finder.html            # every disc, one HTML page (never overwrites)
dubcrate export-finder ~/Desktop/one.html --disc 3      # only disc 3
dubcrate export-finder ~/Desktop/f.html --keys musical # keys as A min, not 8A (default: camelot)
```

One self-contained page with the data inside and no network use: type part of a title or artist, get the disc and the
track number (an MP3 disc also its folder), and under the artist the track's BPM, key, year, genre and length when known. Case, accents and apostrophes don't matter. The text is what is written on
the disc, so it matches the printed label. **A phone has to open it in something that runs local HTML**: an iPhone's
Files app and Safari don't run its script (Sitecase does: AirDrop the file, tap Open in Sitecase). The window's Disc > Export Track Finder… explains this in a box after saving.

## Cue timing (what sync does to each track's cues)

```sh
dubcrate cues timing 3 4      # every track of discs 3 and 4: the offset, its group, how far to trust it
```

rekordbox's times on an MP3 are later than the audio on the disc (`cue_timing`, `dev_docs/cue-timing.md`): 0, about 25 or
about 51 ms depending on the file's first frame, and sync moves each cue earlier by that. This prints it per track (`lame`,
`ffmpeg`, `no-info`, `left-alone`, `not-mp3`; the ffmpeg and left-alone ones are marked less certain), a count per group and one
track of each group to try on the player. It reads the first few kilobytes of each file and nothing else.

## Burning

`dubcrate doctor` says what Dubcrate finds on this machine (ffmpeg and ffprobe, the burners and drive, the disc in the drive) and is the first thing to run when a burn misbehaves.

```sh
dubcrate burn 1                               # dry run: checks the disc, burns nothing
dubcrate burn 1 --yes --simulate              # laser-off test write; needs a disc in the drive
dubcrate burn 1 --yes                         # really burns; uses up the blank CD-R
```

`--levels match [--target LUFS]` matches the tracks' loudness, and `--levels peak` brings each track's highest peak to -0.3 dBTP (audio CDs; also on `export`).

Options: `--backend discrecording|cdrdao` (default `discrecording` on a Mac, `cdrdao` elsewhere), `--speed N`
(default 10, which is the slowest this project's drive offers), `--device` (a drive's id: a BSD name such as `disk3` with DiscRecording,
a device such as `/dev/sr0` with `cdrdao`; the window's drive menu picks the same thing), and
`--work DIR` to keep the rendered WAVs instead of using a temp folder. The command line has no way to stop a burn
once it is started (the window can cancel while the disc is being prepared, not while it is written).

**A real burn uses up a disc.** Use a blank CD-R, not a CD-RW. `burn` refuses a disc
that is over its size, empty, or has a missing or stale track. With `--yes` it also asks
the drive what the inserted disc holds, prints it, and refuses if the audio won't fit
(or there is no disc). A drive that can't say is not a reason to refuse.
A real burn marks the disc as burned (a note on the disc, which stays editable); `--simulate` doesn't. See
[burning.md](burning.md) for what has and hasn't been tried on hardware.

## The CDJ-1000MK3's SD card

```sh
dubcrate card show /Volumes/DISK_IMG          # the discs, cues, loops, hot cues and waveforms on a card
dubcrate card show ~/cdj-card/files/X --waveforms
dubcrate card backup /Volumes/DISK_IMG        # copy the card's few kilobytes aside
dubcrate card format                          # removable disks, and whether each could be a CDJ card (macOS)
dubcrate card format disk4                    # dry run: checks it (plain SD, 2 GB or less) and says what would be erased
dubcrate card format disk4 --yes              # ERASES it: asks you to type disk4; MBR + FAT16 + PIONEER/CDJ, left mounted
dubcrate card format disk4 --yes --confirm disk4   # the same without being asked (for scripts: type the disk's name here instead)
dubcrate card sync 3 4 --card /Volumes/DISK_IMG          # dry run: what would be written
dubcrate card sync 3 4 --card /Volumes/DISK_IMG --yes    # back up, write, read back (cues AND waveforms)
dubcrate card sync 3 4 --card /Volumes/DISK_IMG --no-waveforms --yes   # cues only
dubcrate card check-waveforms /Volumes/DISK_IMG --toc "CD TOC.txt" --audio DIR   # the model against what the player wrote
dubcrate card toc                              # the TOC of the audio CD in the drive (macOS) and its card ID
dubcrate card toc 3                            # ... and whether disc 3 as planned has exactly that TOC
dubcrate card sync 3 --card /Volumes/DISK_IMG --from-drive   # one disc: its ID from the CD in the drive
```

The card argument is the card's root, its `PIONEER` folder, or `PIONEER/CDJ` (a mounted card or a copy
of that folder). `sync` takes **audio disc ids** (see `dubcrate disc list`): every **rekordbox cue** on
their tracks (memory cues, hot cues, loops) becomes a **memory cue** on the card, hot cue slots are
never touched, and cues already on the card are kept. It measures the tracks' lengths first (the card
entry is the disc's TOC), skips and names a disc it can't sync (MP3, one track, a length that can't be
measured), warns when a disc isn't marked as burned or has gaps, and is a **dry run unless `--yes`**.
With `--yes` it copies the card's files to `--backup-dir` (default `card-backups` next to the
library) before writing, writes the disc files and then the index, and reads them back.
A disc's **waveforms** (the CDJ's own, worked out by `wave_model` from each track and cached on it; a burn
does this already, otherwise `sync` does it first, a few tenths of a second a track) are written too, **but only for a
track the card has no waveform for**: one it has, the player's or ours, is never changed. `--no-waveforms` leaves them out.
`check-waveforms` compares the model with the records the player wrote for a disc you played: give it the disc's
`drutil toc` output (or `--from-drive`) and a folder with the disc's tracks, each file named with its track number first
(an audio CD mounted on a Mac shows `1 Audio Track.aiff`, ...), and it prints how close the model is, track by track.
`card toc` runs `drutil toc` (a Mac only; **not tried on a drive yet**). `--from-drive` takes the card entry's
ID and the cue positions from the disc in the drive instead of computing them from the tracks, so it works for
a disc edited after its burn, needs no measured lengths, and warns if the disc differs from the plan.

**Cues, and waveform records, written this way have been loaded and shown by a CDJ-1000MK3** (firmware 2.20; see
[card-sync.md](card-sync.md)); `card format` has not been shown to a player yet. Read [card-sync.md](card-sync.md) (the
plan and what to test first) and [cdj1000mk3-card.md](cdj1000mk3-card.md) before using `--yes` on a
card you care about: keep an image of it (`spikes/cdj_card_snap.sh`) so it can be put back.


## Writing memory cues to rekordbox

```sh
dubcrate rekordbox write-memory-cues            # dry run: each edited track's memory cues now and after
dubcrate rekordbox write-memory-cues --yes      # rekordbox CLOSED: back up master.db, write, read back, restore on failure
dubcrate rekordbox write-memory-cues --loops    # also the memory loops saved in Dubcrate (added) and those deleted in Dubcrate (soft deleted)
dubcrate rekordbox write-memory-cues --db COPY/master.db --backup-dir DIR --keep-edits
```

The memory cues edited in Dubcrate's player are put into rekordbox's `master.db` and nothing else is written
(`rekordbox-cue-writes.md`, "Stage 3 as built"). It refuses while rekordbox runs or on a table layout it doesn't know,
copies the database's files to `rekordbox-backups` next to the library first, and puts them back if the read-back fails.
Dubcrate's own edits for the written tracks are dropped afterwards unless `--keep-edits`. `--loops` also writes memory loops
(a loop is added or removed, never changed; hot cues and a hot cue's loop are never written). **The command was run by the owner on their
live library (memory cues and adding a loop: both worked); removing a loop has been tried on synthetic databases only.** Use `--db` on a
copy first.
