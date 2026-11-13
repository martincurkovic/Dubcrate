# The `dubcrate` command-line tool

Everything the window does with the library can also be done from a terminal, and
**burning currently can only be done here**. Run it with `uv run dubcrate ...`.

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
dubcrate disc list
dubcrate disc show 1                          # tracks, times, capacity
dubcrate disc add 1 --node 40                 # a whole playlist, in order
dubcrate disc add 1 --node 44 --fit           # only what still fits (--strict stops at the first misfit)
dubcrate disc add 1 --track 1475 1473 --at 1  # specific tracks, at a position (1-based)
dubcrate disc move 1 3 1                      # move track 3 to position 1
dubcrate disc remove 1 2                      # remove track 2
dubcrate disc set 1 --name "New name" --minutes 74 --pregap 0
dubcrate disc split --node 44 --name "Pop"    # as many discs as it takes, order kept
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

## Burning

```sh
dubcrate burn 1                               # dry run: checks the disc, burns nothing
dubcrate burn 1 --yes --simulate              # laser-off test write; needs a disc in the drive
dubcrate burn 1 --yes                         # really burns; uses up the blank CD-R
```

`--levels match [--target LUFS]` matches the tracks' loudness, and `--levels peak` brings each track's highest peak to -0.3 dBTP (audio CDs; also on `export`).

Options: `--backend discrecording|cdrdao` (default `discrecording` on a Mac, `cdrdao` elsewhere), `--speed N`
(default 10, which is the slowest this project's drive offers), `--device`, and
`--work DIR` to keep the rendered WAVs instead of using a temp folder.

**A real burn uses up a disc.** Use a blank CD-R, not a CD-RW. `burn` refuses a disc
that is over its size, empty, or has a missing or stale track. With `--yes` it also asks
the drive what the inserted disc holds, prints it, and refuses if the audio won't fit
(or there is no disc). A drive that can't say is not a reason to refuse.
A real burn marks the disc as burned (a note on the disc, which stays editable); `--simulate` doesn't. See
[burning.md](burning.md) for what has and hasn't been tried on hardware.
