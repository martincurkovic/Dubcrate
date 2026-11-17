# Building From Source

## Prerequisites

- [uv](https://docs.astral.sh/uv/), which fetches Python 3.13 and everything in `pyproject.toml`
- A C compiler for Nuitka, which compiles the app
- **macOS 14 or newer** (the Qt 6.12 wheels need it) with the Xcode Command Line Tools (`xcode-select --install`). Its build script is `scripts/build_macos.sh`
- **Linux** (x86_64 or aarch64; Ubuntu 24.04 is what CI and the Docker build use): `sudo apt install build-essential patchelf curl xz-utils nasm zip file zlib1g-dev`. Its build script is `scripts/build_linux.sh`, which makes an **AppImage**; with Docker running, `scripts/build_linux_docker.sh` does the whole build in a container, from any computer (see below)
- **Windows 10 or 11 (64-bit)**: the Visual Studio Build Tools' C++ workload (or let Nuitka download MinGW), [MSYS2](https://www.msys2.org) (to build ffmpeg: `winget install MSYS2.MSYS2`, then in an MSYS2 MINGW64 shell `pacman -S --needed base-devel diffutils make nasm xz curl mingw-w64-x86_64-gcc mingw-w64-x86_64-zlib mingw-w64-x86_64-pkgconf`) and [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`). Its build script is `scripts/build_windows.ps1`, which makes an **installer**
- `ffmpeg` and `ffprobe` to run the app from source (`brew install ffmpeg` on a Mac, `sudo apt install ffmpeg` on Linux). The built app carries its own (see below)
- `nasm` on an Intel Mac or an x86_64 Linux, to build those (`brew install nasm`, `sudo apt install nasm`)

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

To check a finished build without opening it, run its executable with `--smoke-test`. It checks the bundled data files, Qt's SVG support, the audio decoder, that the bundled ffmpeg and ffprobe work, SQLCipher (for rekordbox), Apple's DiscRecording bridge, the certificate bundle for the update check, and that the splash and main window build, then exits 0 (or 1 with a report of what's missing). It never touches your settings, your library, rekordbox or a drive. CI does this for every build.

```
QT_QPA_PLATFORM=offscreen src/Dubcrate.app/Contents/MacOS/main --smoke-test=report.txt
```

**ffmpeg and ffprobe are bundled.** `scripts/build_macos.sh` first runs `scripts/build_ffmpeg_macos.sh`, which downloads FFmpeg and LAME (pinned versions, checked against SHA-256 hashes written in the script) and builds them for the Mac it runs on: LGPL (no `--enable-gpl`, no `--enable-nonfree`), static, with the LAME MP3 encoder linked in, and only the decoders, filters, encoders and muxers Dubcrate uses. The result is `build/ffmpeg/<arch>/` (about 40 MB of programs; the script refuses a build that is GPL or links anything outside macOS) and is built once, so later app builds reuse it (`--force` builds it again; delete `build/ffmpeg` to start clean). The app build copies it to `dubcrate/bin` inside the app, sets the executable bit and signs each program, and the smoke test then fails unless both are bundled and can decode a WAV, render a disc track and encode an MP3. `src/dubcrate/tools.py` looks in that folder first and then on `PATH`. To update FFmpeg or LAME, change the version and the hash in the script together, and the notice in `ui/help/THIRD_PARTY_NOTICES.md`. A new build needs the licence texts and the source offer that the script writes to `licenses/` (they ship with the programs).

**Not signed.** The app is ad-hoc signed only, so macOS asks for a click-through the first time ([README](README.md#download--installation)).

## Building for Linux (an AppImage)

`scripts/build_linux.sh` does what the macOS script does, without the Mac parts: `uv sync`, the version from the git tag, `scripts/build_ffmpeg_linux.sh` (the same pinned FFmpeg and LAME as the Mac, built as LGPL and checked: it must not be a GPL build and must need nothing but the C library and zlib), `pyside6-deploy` (Nuitka, standalone, `src/Dubcrate.dist/main.bin`), and then the AppImage: an AppDir with the app, `assets/linux/Dubcrate.desktop`, the icon (`assets/icon/Dubcrate-512.png`, scaled down from the master) and an `AppRun`, made into one file by `appimagetool` (pinned version, checked against a SHA-256 hash in the script, run with `--appimage-extract-and-run` so it needs no FUSE). The output is `Dubcrate-linux-<arch>.AppImage` and a `.zip` of it in the repository root (the zip is what CI uploads: it keeps the executable bit).

```
scripts/build_linux_docker.sh   # builds in an Ubuntu 24.04 container (from a Mac too), opens the result once, writes build/linux/
chmod +x Dubcrate-linux-x86_64.AppImage && ./Dubcrate-linux-x86_64.AppImage
```

The Docker build on an Apple Silicon Mac makes an **aarch64** AppImage; the GitHub runner makes the x86_64 one. It was built and smoke-tested in Docker, but **has not been run on a real Linux desktop** (a window on screen, sound, a drive): see `dev_docs/linux-test-plan.md`. An AppImage built on Ubuntu 24.04 needs a distribution with glibc 2.39 or newer. `cdrdao` is GPL and is not bundled: install it with the package manager to burn.

## Building for Windows (an installer)

`scripts/build_windows.ps1` (run it from PowerShell) does what the Linux script does: `uv sync`, the icon (`scripts/make_ico.py` makes `assets/icon/Dubcrate.ico`, not tracked, from the 1024 px master), the version from the git tag (with a four-part product version for Windows), `scripts/build_ffmpeg_windows.sh` run through MSYS2 (the same pinned FFmpeg and LAME as the other two, LGPL, static, checked to be no GPL build and to need only DLLs every Windows has; set `MSYS2_ROOT` if MSYS2 is not in `C:\msys64`), `pyside6-deploy` (Nuitka, standalone, `src\Dubcrate.dist\main.exe`, no console window), and then, if Inno Setup is installed, `assets/windows/Dubcrate.iss`, which makes `Dubcrate-windows-x64-Setup.exe` in the repository root. The installer installs for the current user (no administrator prompt) on Windows 10 (1809) or newer. Its `AppId` is the program's identity for Windows' upgrades and uninstaller: **never change it**.

```
.\scripts\build_windows.ps1
src\Dubcrate.dist\main.exe --smoke-test=report.txt     # the same check as on the other systems
```

**Not yet run.** The Windows scripts, the installer and the CI job were written from JV Studio's and the Linux scripts and checked only by `tests/test_build_files.py`; the first CI run is the first build. `cdrdao` is not bundled and has no tested Windows build, so on Windows the burn window says it can't burn and **Export Disc as Files…** is the way to a disc.

## Icons and the DMG background

`assets/icon/AppIcon.png` is the master: 1024x1024, already in macOS icon form (rounded square, transparent corners; see `AppIcon.txt`). `scripts/make_icons.sh` makes `assets/icon/Dubcrate-macos.icns` from it on every build, and the `.icns` is not tracked.

`assets/dmg/dmg_background.tiff` (the "Drag Dubcrate to the Applications folder" picture) is tracked, so a build needs no font or tool beyond what is in the repository. Regenerate it with `uv run python assets/dmg/create_dmg_background.py` (macOS only). Its window size and icon positions must match `scripts/dmg_settings.py`; `tests/test_build_files.py` checks that they do.

## CI

- `.github/workflows/test.yml`: lint (`ruff`) and the tests on Linux, on every push, to any branch. `test-macos.yml` and `test-windows.yml` run the same tests on those systems, by hand only (Actions tab > Run workflow), to spare the Actions quota; a release (`build.yml`) also runs the macOS one.
- `.github/workflows/build.yml`: on a `v*.*.*` tag (or by hand): the tests, then the macOS Apple Silicon and Intel builds, the Linux (AppImage, x86_64) build and the Windows installer build (on `windows-latest`, with MSYS2 for ffmpeg), each smoke-tested, then a **draft** GitHub release with the two DMGs, the Linux zip and the Windows installer. Publish the draft to release.
