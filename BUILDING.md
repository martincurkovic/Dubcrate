# Building From Source

## Prerequisites

- [uv](https://docs.astral.sh/uv/), which fetches Python 3.13 and everything in `pyproject.toml`
- A C compiler for Nuitka, which compiles the app
- **macOS 14 or newer** (the Qt 6.12 wheels need it) with the Xcode Command Line Tools (`xcode-select --install`). Only macOS is supported, so there is only one build script
- `ffmpeg` and `ffprobe` to run the app (`brew install ffmpeg`). They are not bundled yet

## Running From Source

```sh
uv sync
uv run dubcrate-gui    # or: uv run python -m dubcrate.ui
uv run pytest          # the tests (they run on Qt's offscreen platform and never touch a drive, a sound card or the network)
```

## Setup and Build

1. Clone the repository
2. Run `scripts/build_macos.sh` (you may need `chmod +x scripts/*.sh`)
3. The build lands in `src/Dubcrate.app`, and the script also writes `Dubcrate-macos-{arm,intel}.dmg` in the repository root (whichever matches your Mac): a window with the app, an Applications shortcut and a background picture (`assets/dmg/`, laid out by `scripts/dmg_settings.py`)

The script syncs the environment, makes the app icon from `assets/icon/AppIcon.png` (`scripts/make_icons.sh`), writes the version from the nearest git tag into `src/dubcrate/_build_version.py` (not tracked, and removed again when the script ends), generates `src/pysidedeploy.spec` (not tracked) and runs `pyside6-deploy` on `src/main.py`. The built app contains the stylesheet templates, the icons and the System 6 font, the user guide and the third-party notices, the app icon, and `LICENSE`.

**The version.** It is the nearest git tag with any leading `v` removed (`v0.2.0` becomes `0.2.0`), found with `git describe --tags --abbrev=0`. That is what the splash screen, About and the update check use, and it is the app's version in Finder. A build with no tag in its history says `0.0.0-dev`, and a build from a commit after a tag still says that tag. The update check compares it with the version numbers of the published releases, so tag releases as `vMAJOR.MINOR.PATCH`.

To check a finished build without opening it, run its executable with `--smoke-test`. It checks the bundled data files, Qt's SVG support, the audio decoder, SQLCipher (for rekordbox), Apple's DiscRecording bridge, the certificate bundle for the update check, and that the splash and main window build, then exits 0 (or 1 with a report of what's missing). It never touches your settings, your library, rekordbox or a drive. CI does this for every build.

```
QT_QPA_PLATFORM=offscreen src/Dubcrate.app/Contents/MacOS/main --smoke-test=report.txt
```

**Not signed.** The app is ad-hoc signed only, so macOS asks for a click-through the first time ([README](README.md#download--installation)).

## Icons and the DMG background

`assets/icon/AppIcon.png` is the master: 1024x1024, already in macOS icon form (rounded square, transparent corners; see `AppIcon.txt`). `scripts/make_icons.sh` makes `assets/icon/Dubcrate-macos.icns` from it on every build, and the `.icns` is not tracked.

`assets/dmg/dmg_background.tiff` (the "Drag Dubcrate to the Applications folder" picture) is tracked, so a build needs no font or tool beyond what is in the repository. Regenerate it with `uv run python assets/dmg/create_dmg_background.py` (macOS only). Its window size and icon positions must match `scripts/dmg_settings.py`; `tests/test_build_files.py` checks that they do.

## CI

- `.github/workflows/test.yml`: lint (`ruff`) and the tests, on every push, to any branch.
- `.github/workflows/build.yml`: on a `v*.*.*` tag (or by hand): the tests, then the macOS Apple Silicon and Intel builds, each smoke-tested, then a **draft** GitHub release with both DMGs. Publish the draft to release.
