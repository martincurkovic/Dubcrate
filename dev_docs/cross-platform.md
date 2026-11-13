# Cross-platform support: where we are and the plan

Dubcrate supports **macOS only** today. This file is for whoever adds Linux or Windows: what
is already portable, what is not, what was decided and why, and the order to do it in.

Notes are tagged as in [burning.md](burning.md): **[hardware]** measured on a real drive or
player, **[tests]** covered by the automated tests, **[not tried]** written or researched but
never run. Research below was done on 2026-11-12 from documentation and web sources, not by
running anything on Linux or Windows; treat every claim about those systems as **[not tried]**
until a person with that OS and an optical drive has checked it.

## The decision so far

- **Audio discs (with CD-TEXT) on Linux and Windows: use `cdrdao`.** Reasons: it is built for
  disc-at-once audio with CD-TEXT, it is still released (1.2.6 on 2025-12-05, 1.2.5 on 2023-02-03),
  it runs as a subprocess (our rule: bundled tools are never linked), and **`burn/cdrdao.py`
  already exists** (TOC generation is covered by tests; the burn itself has never been run).
  CD-TEXT is a hard requirement for Dubcrate (the CDJs show it), which rules out anything that
  can't write it.
- **MP3 discs (data discs, no CD-TEXT)** need an ISO image and a data-disc writer. `cdrdao` can
  write a data track from an ISO (a `TRACK MODE1` entry with `DATAFILE` in the TOC) **[not tried]**,
  so the same backend could cover them once we can build the ISO (see Phase 3). On Windows,
  IMAPI2 (built in) is the alternative for data discs only.
- **Do not plan on Windows' IMAPI2 for audio discs.** Its audio interface
  (`IDiscFormat2TrackAtOnce`) takes raw 44.1 kHz stereo samples and documents nothing about
  CD-TEXT; the only conceivable route is the raw disc-at-once interface
  (`IRawCDImageCreator` + `IDiscFormat2RawCD`) with hand-made R-W sub-channel data, which is
  undocumented for CD-TEXT, would need our own CD-TEXT encoder, and one fork reports a hard
  error from raw mode on a real drive. **Unverified, not recommended.**
- **Ship an Export option early.** Rendering is already separate from burning, so exporting
  the rendered WAVs plus the `cdrdao` TOC (or the staged MP3 folder / an ISO) lets people on any
  OS burn with a tool they already have. That makes Dubcrate useful on Linux and Windows before a
  burn backend is proven, and it is cheap.
- **Order of work:** groundwork and CI first (no drive needed), then Export, then Linux (the
  easiest burn), then Windows (the hardest), then packaging for each.

## What is portable already

| Area | State |
| --- | --- |
| Library, planning, discs, folders, the MP3 and CBR logic, CD-TEXT cleaning | plain Python **[tests]** |
| UI | PySide6, which runs on all three; tests run offscreen **[tests]** (on macOS only so far) |
| Audio preview | miniaudio (cross-platform) |
| Rendering and probing | `ffmpeg` / `ffprobe`: `tools.find_tool` looks in the app's bundle (`dubcrate/bin`, macOS builds only so far), then `PATH` |
| App data, log folder | `platformdirs` (`library/paths.py`, `logsetup.py`); the theme's icon cache has `darwin` / `win32` / other branches |
| rekordbox library location | `library/rekordbox.py: default_master_db` has macOS and Windows paths, and returns `None` on Linux (rekordbox doesn't exist there; a copied `master.db` can still be picked by hand) |
| Label printing | `QPrinter` / `QPdfWriter` and a painter (`ui/label_paint.py`), so portable in principle; **[not tried]** off macOS, and not tried on a real printer anywhere (PDF output is tested) |
| Shortcuts | written as `Ctrl+...`; Qt shows ⌘ on a Mac. The shortcuts window uses native text |
| Burner interface | `burn/base.py` `Burner` protocol, built so a backend can be added without touching callers |

## What is macOS-only, and where

| Where | What | What to do |
| --- | --- | --- |
| `burn/discrecording.py`, `pyproject.toml` (`pyobjc-framework-discrecording ; sys_platform == 'darwin'`) | The only backend that writes CD-TEXT discs and MP3 data discs. Already guarded by a platform check | Keep as the macOS backend |
| `ui/burn_dialog.py: _backend()` and the CLI's `--backend` default | Hard-code `get_burner("discrecording")` | Choose the backend by platform in one function (Phase 0) |
| `burn/cdrdao.py` | `media_info` returns everything unknown (no free-space check); first device only; progress is raw lines; `burn_data` raises "audio CDs only" | Harden (Phase 3) |
| `ui/app.py: extend_tool_path`, `TOOL_DIRS` | Adds Homebrew's folders so a Finder-launched app finds ffmpeg | Generalise: Linux needs nothing; Windows needs the bundled tools' folder |
| `scripts/build_macos.sh`, `scripts/dmg_settings.py`, `assets/dmg/`, `BUILDING.md` | Packaging is a macOS app and a DMG | One build per OS (Phase 5) |
| `.github/workflows/test.yml` | Runs on `macos-15` only, and says so in a comment | Matrix (Phase 1) |
| `.github/workflows/build.yml` | macOS arm and Intel builds; the release job is the only Linux one | Add per-OS builds (Phase 5) |
| `ui/explorer.py: mounted_volumes()` | Finds drives in macOS's `/Volumes` (dropping the boot-disk link); returns none elsewhere, so the Explorer shows only Music and home | Linux (`/media`, `/run/media`, `/mnt`) and Windows (drive letters) **[not tried]** |
| `ui/smoke_test.py` | Has a `darwin` check (DiscRecording, built-app specifics) | Per-OS smoke test |
| `ui/help/manual.md`, `README.md`, `CONTRIBUTING.md` | Mention Homebrew, `~/Library/Application Support/Dubcrate/`, macOS-only burning | Say what each OS needs, per OS |
| `tests/test_app_bundle.py`, `tests/test_build_files.py` | Check the macOS build files | Skip off macOS (Phase 1) |

## Checked with real tools in a Linux container (2026-11-13, no drive)

Docker on a Mac, Ubuntu 24.04, cdrdao 1.2.4: findings, all **[tests]** (not hardware).
- Our TOC is valid; lengths and gaplessness come out right (`cdrdao show-toc`).
- Relative file names in a TOC are resolved against the **current directory**, not the TOC's folder.
- A track under **4 seconds** is refused by cdrdao unless `--force`: now caught before the burn.
- `cdrdao write` asks "hit enter" when it wants a disc: our run closes its stdin.
- `cdrdao scanbus` lists drives through sysfs; without one it prints `Unable to glob through sysfs`.
- cdrdao's failure texts for "no such device" and an unusable device are mapped to plain messages (the
  permission-denied text is assumed to follow the same shape: **[not tried]**).
- Qt's X11 plugin needs `libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0
  libxcb-xkb1 libxkbcommon-x11-0`; the app then runs on Xvfb and draws correctly with the system font.
  Sound, a real window manager, HiDPI, Wayland and printing are **[not tried]**.
- The next step is [linux-test-plan.md](linux-test-plan.md), on a real Linux machine.

## Roadmap

### Phase 0: groundwork (no behaviour change, no drive needed): done 2026-11-13

- [x] `burn.default_backend()` ("discrecording" on macOS, "cdrdao" elsewhere), used by
  `burn_dialog._backend()` and the CLI's `--backend`. (The spike still asks for `--backend`.)
- [x] `burn.backend_problem(name)`: the burn window says up front, in plain words, when no
  backend can run (no `cdrdao`, or DiscRecording off a Mac), never starts a burner, and points
  at Export. The install hint is per OS (`cdrdao.install_hint`). **[tests]**
- [x] `tools.find_tool(name)` (bundled folder, then `PATH`; `.exe` on Windows) is used for
  ffmpeg, ffprobe and `cdrdao`; `tools.install_command()` is per OS. `extend_tool_path` stays,
  adding Homebrew's folders on macOS only.
- [x] Drives in the Explorer: `_mac_volumes`, `_linux_volumes` (mount points under
  `/media/<user>`, `/run/media/<user>`, `/media`, `/mnt`) and `_windows_volumes` (GetLogicalDrives
  and GetDriveType; no system or CD drives), one helper each. The helpers are tested with fake
  folders and drive lists; reading the real Linux and Windows lists is **[not tried]**.

### Phase 1: CI on Linux and Windows (finds problems cheaply)

- [x] `test.yml`: a matrix of `macos-15`, `ubuntu-latest`, `windows-latest`, ffmpeg installed per
  OS (`brew`, `apt-get` with Qt's system libraries, `choco`). **Linux is the default leg** (first,
  gates the run, runs the lint); macOS gates too (the shipped app is a Mac one). Windows is
  `experimental` (its failures show but don't fail the run, so can't block a release) until green:
  then remove the flag. **Written 2026-11-13, not yet run on GitHub.**
- [x] Linux in Docker: `scripts/test_linux.sh` (Ubuntu 24.04, same system packages as the CI leg;
  `scripts/linux-test.Dockerfile`) runs the suite on Linux from a Mac. First run (Arm Linux,
  2026-11-13): 1223 of 1226 passed and 4 skipped; the 3 failures were real portability bugs
  (a narrow table column opened wider because Qt's minimum header width is larger on Linux; a
  shortcut search for "space" also matched "Backspace"), fixed. **[tests]** on Linux, **[not tried]**
  with a display, a sound card or a drive. PySide6's Linux wheels need glibc 2.34 (Intel) or 2.39 (Arm).
- [ ] Skip the macOS-specific tests off macOS (`test_app_bundle.py`, `test_build_files.py`;
  `test_mp3_discs.py` already skips the DiscRecording test).
- [ ] Expect some failures on first run (paths, line endings, file locking on Windows,
  `pyrekordbox`'s SQLCipher dependency). Fix or mark each with a reason; do not delete tests.
- [ ] Check what `pyrekordbox` needs on Windows and Linux for SQLCipher (it worked on the Mac).
  It is the known bundling risk in `AGENTS.md`.

### Phase 2: Export (useful on any OS before any burn backend works): done 2026-11-13

- [x] `dubcrate export DISC DIR` and **Export…** in the burn window (`export.py`): for an audio
  disc, numbered ASCII-named WAVs plus `disc.toc` (`build_toc(..., relative_to=folder)`, CD-TEXT
  included) and a README; for an MP3 disc, the staged folder with the README beside it. A new
  folder each time, never overwriting; a failed export removes its own half-made folder. **[tests]**
  Still to do: an ISO for MP3 discs, once Phase 3 can build one.
- [x] The README says how to burn it with `cdrdao write disc.toc` (from inside the folder, since
  the TOC's file names are relative: whether cdrdao resolves them against the TOC's folder or the
  working directory: **checked with cdrdao 1.2.4 in Docker: the working directory**, so running it from
  the folder is required), K3b/Brasero, Finder, Explorer and `xorriso`. **None of those steps was run.**

### Phase 3: Linux (the easiest burn)

- [x] Harden `burn/cdrdao.py` (done 2026-11-13 against cdrdao 1.2.4's own strings and error output; **no drive was involved**, so none of it is **[hardware]**):
  - `media_info` from `cdrdao disk-info --device X` (blank? capacity?), parsed defensively,
    with "unknown" always allowed (the existing rule: an unknown is never a reason to refuse);
  - choose the drive (`scan_devices` exists), and pass it through `BurnOptions.device`;
  - progress lines to percentages for the burn window;
  - the quirks below (eject and reinsert after a simulation). CD-TEXT is switched on by the
    `CD_TEXT` block our TOC already contains; check every flag we pass against the installed
    version's `--help`, since releases differ.
- [x] MP3 discs, built (`iso.py`, `cdrdao.build_data_toc`, `CdrdaoBurner.burn_data`): the ISO (ISO 9660 +
  Joliet + Rock Ridge) comes from `xorriso -as mkisofs` (else `genisoimage` / `mkisofs`; `hdiutil` on a Mac),
  and is written as one `TRACK MODE1` data track. Export makes the same `disc.iso`. **[tests]**: xorriso's
  image was read back with `isoinfo` (Joliet names, volume id) and cdrdao's `show-toc` accepts the TOC. The
  write itself is **[not tried]**; the alternative is `xorriso -as cdrecord ... disc.iso`. Decide by trying both
  on a real drive (step 6 of [linux-test-plan.md](linux-test-plan.md)).
- [ ] Hardware checks, in the style of `burning.md`: drive detection, a simulated audio burn, a
  real audio burn on a CDJ-1000MK3 (gapless boundaries, CD-TEXT), an MP3 disc. Tag results
  **[hardware DATE]**.
- [ ] Device permissions: the user must be allowed to open the drive (often the `cdrom` or
  `optical` group, or a udev rule). Detect "permission denied" and say so.
- [ ] Packaging: an AppImage or Flatpak (Flatpak needs device access granted), or a plain
  `pipx`/`uv tool` install with ffmpeg and cdrdao from the package manager. Decide with the
  first Linux user.
- [ ] The rekordbox importer has no default path on Linux: folders only, or pick a copied
  `master.db`. Say so in the guide.

### Phase 4: Windows (the hardest burn)

- [ ] **Spike first, on a real Windows machine with a drive**, before any design:
  `cdrdao scanbus`, `cdrdao disk-info`, a simulated audio burn with CD-TEXT, one real disc
  checked on a CDJ. `spikes/burn_spike.py` already drives a backend (`--backend cdrdao`, dry run
  by default) and `burning.md` has the hardware check steps.
- [ ] Get a trustworthy `cdrdao.exe`. The project publishes source (1.2.6 notes mention a native
  Windows interface; an older note says the port was Cygwin-based). Build it with MSYS2 or
  Cygwin in CI and keep it in a bundled folder. Licence: check (it is GPL; we run it as a
  subprocess, and a bundled copy needs an entry in `ui/help/THIRD_PARTY_NOTICES.md`).
- [ ] Administrator rights: unknown. Sources about `cdrecord`'s SPTI access disagree and are old.
  Test on Windows 10 and 11 with and without elevation. If elevation is needed, say so in the burn
  window.
- [ ] Fallbacks if `cdrdao` fails there: `chrdev/cdrecord` (a `cdrecord` fork aimed at audio CDs
  with CD-TEXT, with Windows releases; small hobby project) or cdrtools. `ISOSDK` and FoxBurner
  (commercial) list CD-TEXT; their licences and Python access need checking first.
- [ ] MP3 discs on Windows: IMAPI2's data interface (`IDiscFormat2Data` with `IFileSystemImage`)
  needs no extra programs and CD-TEXT is irrelevant for data discs. Reach it from Python with
  `comtypes` or `pywin32` (a new dependency: GPL-compatible? add the notice).
- [ ] Installer and code signing (SmartScreen). Nuitka builds one per OS; there is no
  `pyside6-deploy` cross-compilation.
- [ ] Paths: `platformdirs` already; double-check file locking when a library is open.

### Phase 5: packaging and release

- [ ] `build.yml`: a job per OS producing an installer or archive; the smoke test
  (`--smoke-test=FILE`) runs against each build, as for macOS. The release job already runs on Linux.
- [ ] ffmpeg: macOS bundles an LGPL build (`scripts/build_ffmpeg_macos.sh`, looked up by
  `tools.find_tool` before `PATH`). Linux and Windows still need their own: the same flags would
  do (LGPL, static, LAME built in), but nothing was tried there. Decide per OS.
- [ ] Update `BUILDING.md`, `README.md`, `CONTRIBUTING.md` and the guide per OS.

## What we know about cdrdao (read before relying on it)

**Confirmed from its documentation and releases**
- Disc-at-once (DAO) only; command line only (`gcdmaster` was the separate GUI).
- Writes from a text description (the TOC file); we already generate it with CD-TEXT
  (`burn/cdrdao.py: build_toc`, covered by tests).
- **CD-TEXT is switched on explicitly, and there is no way to detect whether a drive can write it.**
  A drive that can't would fail or ignore the text; so the hardware check must read the disc back
  on a CDJ, as for DiscRecording.
- README quirks: after a simulation you may need to eject and reinsert the disc before the real
  write; some drivers can't write every field (a Ricoh driver can't write the catalogue
  number); drive addressing is as for `cdrecord` (it can show `ATAPI:0,0,0` where a user
  expected a SCSI address).
- Releases: 1.2.5 (2023-02-03), 1.2.6 (2025-12-05).

**From our own code** (not from cdrdao's documentation)
- No free-space check today, one drive (the first), progress as raw lines, test write via
  `--simulate`, speed via `--speed`, eject via `--eject`. None of it has been run.
- It cannot burn MP3 discs yet (`burn_data` raises).

**Not confirmed**
- Anything about Windows: whether it needs administrator rights, which drive interface it uses,
  and which binary to trust. There is no official Windows binary.
- Whether the `0` pregap TOC we generate gives gapless boundaries on a real drive (the same
  question `burning.md` still has for the DiscRecording path).
- The exact command-line flags on the version a user has: check `cdrdao --help` and test.

## Alternatives considered

| Option | Verdict |
| --- | --- |
| `cdrdao` | **Chosen** for audio (above) |
| cdrtools (`cdrecord`, `mkisofs`) | Mixed licences (mostly CDDL; `mkisofs` GPL; Debian and Fedora moved to the `cdrkit` fork over it); upstream stopped (3.02a9 in 2017, author died 2021; later work is in `schilytools`); Windows builds are third-party. CD-TEXT is possible via info files. A fallback, not a first choice |
| `chrdev/cdrecord` fork | Aimed at audio with CD-TEXT, Windows releases; small project. A fallback |
| IMAPI2 (Windows built-in) | Good for **data** discs; CD-TEXT for audio **not documented** (see above) |
| FoxBurner, ISOSDK, PrimoBurner | SDKs. FoxBurner is commercial; ISOSDK lists CD-TEXT and cue sheets and asks for a donation for commercial use; PrimoBurner's CD-TEXT is unconfirmed. Licence and Python access unchecked |
| Windows Media Player burn API | Legacy; no |
| WSL on Windows | A USB burner is hard to reach from it; no |
| ImgBurn | Proprietary and has a history of bundled adware; cannot be bundled |

## Rules to keep while doing this

- A burner is a backend behind `Burner`; do not leak backend details into callers
  (`AGENTS.md`).
- Never burn without the user's explicit confirmation; spikes are dry runs unless `--yes`.
- External tools run as subprocesses with an explicit `check=`; a bundled ffmpeg is an LGPL build.
- Dubcrate is GPL-3.0-or-later: keep new dependencies GPL-3 compatible and add them to
  `ui/help/THIRD_PARTY_NOTICES.md` (`tests/test_notices.py` fails if a runtime package is missing).
- Tests must not need a drive, a disc or the network. Tag what was run on hardware, with the date,
  and leave everything else **[not tried]**.
- The owner's test rig is a Mac with a LiteOn eSAU108 and two CDJ-1000MK3. **There is no Linux or
  Windows hardware yet**; anything for those systems needs a person with that OS and a drive.

## Sources (research of 2026-11-12)

- cdrdao: [1.2.6 release](https://sourceforge.net/projects/cdrdao/files/rel_1_2_6),
  [VideoHelp page](https://www.videohelp.com/software/CDRDAO),
  [manual](https://dokk.org/manpages/debian/13/cdrdao/cdrdao.1.en),
  [README](https://git.stg.centos.org/source-git/cdrdao/blob/0d29cbcd9ae1207fa327fae2c82a00c3df522a0f/f/README),
  [mailing-list post](https://sourceforge.net/p/cdrdao/mailman/message/1818984),
  [Wikipedia](https://en.wikipedia.org/wiki/Cdrdao)
- cdrtools: [Wikipedia](https://en.wikipedia.org/wiki/Cdrtools),
  [README.win32](https://cdrtools.sourceforge.net/old/private/man/README/README.win32),
  [SPTI thread](https://lists.debian.org/cdwrite/2004/01/msg00065.html),
  [ImgBurn SPTI thread](https://forum.imgburn.com/topic/14640-you-need-administrative-privileges-to-use-spti),
  [cdrtfe](https://www.sourceforge.net/projects/cdrtfe),
  [chrdev/cdrecord](https://github.com/chrdev/cdrecord)
- IMAPI2: [IDiscFormat2TrackAtOnce](https://learn.microsoft.com/en-us/windows/win32/api/imapi2/nn-imapi2-idiscformat2trackatonce),
  [IRawCDImageCreator](https://learn.microsoft.com/en-us/windows/win32/api/imapi2/nn-imapi2-irawcdimagecreator),
  [AddSubcodeRWGenerator](https://learn.microsoft.com/en-us/windows/desktop/api/imapi2/nf-imapi2-irawcdimagecreator-addsubcoderwgenerator),
  [CD subcode (Wikipedia)](https://en.wikipedia.org/wiki/Compact_Disc_subcode),
  [a raw-mode error report](https://github.com/0xfed/DiscImageStudio/pull/9)
- SDKs: [ISOSDK](https://ognif.github.io/isosdk)
