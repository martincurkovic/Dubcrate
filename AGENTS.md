# AGENTS.md

Guidance for AI coding agents working in this repo. See `README.md` for what the
project is.

## Commands

```sh
uv sync                                  # install deps
uv run pytest                            # tests (no hardware needed)
uv run ruff check . && uv run ruff format .
uv run spikes/burn_spike.py doctor       # check tools, drive and media
```

Run ruff and pytest before calling work done. The ruff config comes from outside
the repo; follow whatever it reports.

## Layout

- `src/dubcrate/cd.py`: Red Book constants, sector maths, CD-TEXT string cleaning.
- `src/dubcrate/render.py`: ffmpeg decode to 16-bit/44.1k stereo WAV, padded to whole sectors.
- `src/dubcrate/burn/base.py`: `Disc`, `DiscTrack`, `BurnOptions`, `Burner` protocol.
- `src/dubcrate/burn/discrecording.py`: macOS backend (PyObjC). Import only on darwin.
- `src/dubcrate/burn/cdrdao.py`: cdrdao backend, TOC generation.
- `src/dubcrate/library/`: Dubcrate's own SQLite library.
  - `models.py`: `TrackRecord`/`NodeRecord` (what a source produces) and `Track`/`Node`/`Source` (what the store returns).
  - `store.py`: `Library`. `replace_source` syncs a source in one transaction and keeps track ids stable across re-imports.
  - `folder.py`: scan a folder of audio files (ffprobe tags); directories become browse nodes.
  - `rekordbox.py`: read a rekordbox 6/7 `master.db` via pyrekordbox. Mapping is in pure functions so it is testable with fake rows.
  - `lengths.py`: `measure_tracks` probes tracks and caches their exact CD length (`track.cd_sectors`).
  - Discs live in the same store (`disc`, `disc_track`): `create_disc`, `add_to_disc` (plain, `fit`, `strict`), `remove_from_disc`, `move_in_disc`, `split_into_discs`.
- `src/dubcrate/planning.py`: pure packing algorithms (`fit_tracks`, `split_sequential`) on lists of sector lengths.
- `src/dubcrate/build.py`: `build_burn_disc` turns a planned disc into rendered WAVs plus a `burn.Disc`, re-checking capacity against the rendered lengths. `disc_problems` lists what blocks a burn.
- `src/dubcrate/cli.py`, `cli_discs.py`: the `dubcrate` command (import, sources, nodes, tracks, disc ..., burn) until there is a GUI.
- `spikes/`: throwaway hardware drivers. Reusable logic belongs in the package.
- `tests/`: pytest. Tests must not need a drive or a disc.
- `assets/icon/`: `AppIcon.png` is the 1024x1024 master; `AppIcon.txt` holds the
  icon.kitchen link it came from. Platform icons (.icns, .ico) get derived from
  the master at build time.

## Hard rules

- **Never burn without explicit user confirmation.** Burning consumes a physical
  disc. Spike commands default to a dry run; only pass `--yes` when the user has
  said to burn and has a blank CD-R loaded.
- **Git is handled by a human.** Committing, pushing, branching and merging are
  where code review happens, so they are not the agent's job. An agent may run
  `git add` to stage its work. When a commit is appropriate, suggest a brief
  commit message and stop there; don't run `git commit`, `git push`, `git reset`
  or anything else that changes history or the remote.
- **Never write to a rekordbox database or library.** Reading is fine, and only
  from a snapshot copy (`rekordbox._snapshot`), never the live `master.db`.
  Dubcrate stores its own data in its own SQLite DB.
- **Treat the user's music as read-only.** Scanning and probing only; never
  modify, move or delete audio files. Use `--library <scratch path>` when trying
  the CLI so the user's real app-data library isn't touched.
- Burners are backends behind the `Burner` protocol. Don't leak backend details
  (DiscRecording types, cdrdao flags) into callers.
- Tracks are gapless by default (`BurnOptions.pregap_sectors = 0`). DiscRecording
  would otherwise insert 2 s of silence before every track. Capacity checks must
  count any pregap via `Disc.used_sectors`.
- A track's length for capacity purposes is `Track.length_sectors`: measured if known,
  else a conservative estimate (rekordbox durations are whole seconds). Rendered length
  is authoritative, and `build_burn_disc` writes it back. Never plan with raw `duration`.
- Re-imports must not silently drop a track a disc uses: it is kept and marked `stale`.
- Every track must be a whole number of sectors (588 samples) before it reaches a
  burner. `render_track` guarantees this; keep it that way.
- CD-TEXT is required, not optional. Text goes through `cdtext_clean` first.

## Conventions

- Python 3.13+, type hints, `pathlib.Path` for paths, stdlib where it will do.
- Run external tools with `subprocess` and an explicit `check=` argument.
- Store ids (`Track.id`) are what discs will reference, so re-imports must keep them
  stable: match on `ext_id`, never delete-and-reinsert tracks.
- Keep dependencies light and watch licenses: `mutagen` and anything from rbxport
  are GPL, so they constrain the project license (not yet chosen).
- GUI will be PySide6 and packaged with Nuitka. Avoid dependencies that make
  Nuitka bundling hard (SQLCipher bindings are the known risk).
- Bundled binaries (ffmpeg, cdrdao) are run as subprocesses, never linked. An
  eventual bundled ffmpeg must be an LGPL build.

## Hardware notes

- Test rig: macOS, LiteOn eSAU108 USB burner, a pair of CDJ-1000MK3s.
- macOS labels the drive "Unsupported", but DiscRecording reports it can write
  CD-R, CD-TEXT, SAO and test-write.
- 80 min = 360,000 sectors, 74 min = 333,000. Use 10 to 16x for old players; this drive's slowest speed is 10x.
