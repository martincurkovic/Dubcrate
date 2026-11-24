# Linux test plan: the first session on a real Linux machine

Everything here was prepared on a Mac, with Linux only in a Docker container (no display of its own,
no sound card, no drive). The container run **[tests]** proved what could be proved without hardware;
this plan is what is left, in the order to do it. Tag what you find as in [burning.md](burning.md):
**[hardware DATE]**. Anything not run stays **[not tried]**.

## 0. What is already known (from the container, 2026-11-13)

- The whole test suite passes on Ubuntu 24.04 (`scripts/test_linux.sh`; Arm Linux, so Intel is only
  covered by CI).
- The app starts on a real X11 session (Xvfb) and draws correctly with the system font. Qt's X11
  plugin needs libraries the tests never load: **`libxcb-cursor0 libxcb-icccm4 libxcb-image0
  libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0`**. Without `libxcb-cursor0` the
  app says "Could not load the Qt platform plugin xcb".
- PySide6's Linux wheels need glibc 2.34 (Intel) or 2.39 (Arm): Ubuntu 22.04 or newer, Debian 12 or
  newer on Intel.
- **cdrdao 1.2.4** (Ubuntu 24.04's) reads the `disc.toc` we write: right track lengths, track 2 starting
  exactly where track 1 ends (gapless), and `show-toc` accepts a `CD_ROM` / `TRACK MODE1` TOC for the MP3
  image. Every flag we pass (`--device`, `-n`, `--speed`, `--simulate`, `--eject`) exists.
- **cdrdao resolves relative file names against the current directory, not the TOC's folder.** So run it
  from inside the export folder (the export README says so).
- **cdrdao refuses a track shorter than 4 seconds** ("Use option --force to ignore the warnings").
  Dubcrate says so before burning.
- Not known: what a real drive prints. The `disk-info` parser reads labels taken from cdrdao's binary
  (`CD-R empty`, `Total Capacity`, ...), and the failure messages it recognises are the ones it printed
  without a drive. **Step 5 is where they meet a real one.**

## Results of the first session (2026-11-23, Omarchy / Arch, a real drive and CDJ card)

Ticked below where it was done; **[hardware 2026-11-23]** details are in [cross-platform.md](cross-platform.md)
("The first real Linux machine") and [burning.md](burning.md) ("Linux and `cdrdao`"). Not run: the wrong-case messages in
4, a real printer; section 6 (an MP3 disc) was started and has no recorded result yet. Arch notes: `pacman -S cdrdao libisoburn` (add `fuse2` for
AppImages); cdrdao there is **1.2.6**; USB sticks and cards mount under `/run/media/$USER/`; the drive is opened
through a logind ACL, not a group.

## 1. Set up

```sh
# Debian / Ubuntu
sudo apt install ffmpeg cdrdao xorriso git \
    libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xkb1 libxkbcommon-x11-0
curl -LsSf https://astral.sh/uv/install.sh | sh        # uv fetches Python 3.13
git clone <the repository> && cd Dubcrate
uv sync
uv run pytest                  # expect all passing; note anything that fails or skips unexpectedly
uv run dubcrate-gui --library ~/try.db
```

Fedora: `dnf install ffmpeg-free cdrdao xorriso` (the `ffmpeg-free` package may lack the MP3 encoder:
see step 3) plus the same libraries under their Fedora names. **[not tried]**

Drive permissions: `ls -l /dev/sr0` shows the group (often `cdrom`, or `optical`). Put yourself in it
(`sudo usermod -aG cdrom $USER`), log out and in, and check `id`. A blank CD-R in a drive you can use:
`cdrdao scanbus` should list it, then `cdrdao disk-info --device /dev/sr0` should say `CD-R empty : yes`.

## 2. The app on a real desktop (no drive needed)

- [x] Starts (default, xcb and Wayland plugins; **[hardware 2026-11-23]**). Window looks in all four looks and HiDPI
      scaling: **not reported**. A tiling window manager makes layout checks unrepresentative.
- [x] The log is `~/.local/state/Dubcrate/log/dubcrate.log` and its first lines name the tools it found
      (`ffmpeg ... (found on your PATH)`) **[hardware 2026-11-23]**; Help > Show Log File itself **[not tried]**.
- [ ] Settings > Library lists ffmpeg and ffprobe with paths and versions. Rename `ffmpeg` away
      temporarily: the start-up notice should offer `sudo apt install ffmpeg`.
- [ ] Import a folder of music (File > Import Folder); the Explorer tab lists your home, Music and a
      **USB stick** (`/media/$USER/...`) if one is plugged in. Pin a folder.
- [x] Preview: sound, no lag (owner, PipeWire) **[hardware 2026-11-23]**.
- [ ] Space, arrows and the Ctrl shortcuts (the Shortcuts window shows them as `Ctrl+...`).
- [x] Print Labels: saving a PDF works (owner) **[hardware 2026-11-23]**; the CUPS print panel with a real printer **[not tried anywhere]**.
- [ ] Drag-to-change spin boxes and trackpad pinch in the label preview feel right.

## 3. Export (no drive needed)

- [x] Audio disc export (from the command line) and `cdrdao show-toc disc.toc` read it **[hardware 2026-11-23]**.
- [x] MP3 disc export: `xorriso -indev disc.iso -toc` shows `MP3 TEST` and the files **[hardware 2026-11-23]**;
      mounting the image and looking at the names **[not tried]**.
- [x] ffmpeg 9.0.2 on Arch has `libmp3lame`; a FLAC source encoded to MP3 for the export **[hardware 2026-11-23]**.

## 4. A test burn (laser off)

With a blank CD-R in the drive, **Burn… > Test only** (or `dubcrate burn N --yes --simulate`).

- [x] The burn window shows the drive and the capacity (`disk-info` printed `CD-R empty : yes`, `Total Capacity :
      79:57:71 (359846 blocks, 702/807 MB)`; the parser matched) **[hardware 2026-11-23]**.
- [x] The log shows progress every 10 percent in a real burn **[hardware 2026-11-23]** (a 3-track simulation printed
      33 / 66 / 100 percent).
- [x] A simulation finishes ("Simulation finished successfully") and the disc is not marked burned. **The first one
      failed** with `Cannot set write parameters mode page` and could not be reproduced: see burning.md.
- [ ] Wrong cases give plain messages: no disc in the drive; a disc that is not blank; permission denied
      (take yourself out of the group: the message should name the drive's group and the `usermod` line, and after you
      add yourself again but before logging out, say to log out and in); a speed the drive lacks.
- [ ] After a simulation cdrdao may want the disc reloaded before the real write (its README says so).

## 5. A real audio burn, played on a CDJ

Follow the hardware check in [burning.md](burning.md): a short disc first, 10x to 16x.

- [x] The disc was written (10 tracks, 552 MB, requested 48x, written at 24x), the TOC check said "exactly as planned",
      and the owner says it reads fine **[hardware 2026-11-23]**.
- [x] **CD-TEXT** shows on the CDJ-1000MK3 (owner) **[hardware 2026-11-23]**; the audio sounds perfect.
- [x] Track boundaries are gapless: the owner is fairly sure (a CDJ makes it hard to tell); the TOC is as planned, which
      has no pregap.
- [x] Drive, media and speed are in `burning.md`.

## 6. A real MP3 disc

- [ ] **Burn… on an MP3 disc** (the cdrdao path: ISO, then one `MODE1` data track).
- [x] (not needed: `cdrdao` works, 2026-11-24) If that does not work, the alternative is `xorriso -as cdrecord -v dev=/dev/sr0 speed=10 disc.iso`
      on the exported image: record which one works on this drive and on the CDJ.
- [ ] The CDJ reads it, file order is right, long names show.

## 7. What to write down

Record each result in `cross-platform.md` (tag **[hardware DATE]**) and `burning.md`, and open a note for
each thing that did not work with the exact text on screen, `cdrdao`'s output and the log file
(`~/.local/state/Dubcrate/log/dubcrate.log`).

## Afterwards: packaging

Not started: an AppImage or Flatpak (Flatpak needs device access), or `uv tool install`. Decide with this
session's experience: see Phase 3 in [cross-platform.md](cross-platform.md).
