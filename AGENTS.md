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
- `spikes/`: throwaway hardware drivers. Reusable logic belongs in the package.
- `tests/`: pytest. Tests must not need a drive or a disc.

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
  through a read-only interface. Dubcrate stores its own data in its own SQLite DB.
- Burners are backends behind the `Burner` protocol. Don't leak backend details
  (DiscRecording types, cdrdao flags) into callers.
- Every track must be a whole number of sectors (588 samples) before it reaches a
  burner. `render_track` guarantees this; keep it that way.
- CD-TEXT is required, not optional. Text goes through `cdtext_clean` first.

## Conventions

- Python 3.13+, type hints, `pathlib.Path` for paths, stdlib where it will do.
- Run external tools with `subprocess` and an explicit `check=` argument.
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
