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

- [ ] Starts, window looks right in all four looks (Settings > Appearance); note HiDPI scaling and the font.
- [ ] Help > Show Log File opens the folder: `~/.local/state/Dubcrate/log/` **[not tried]**; the log's first
      lines name the tools it found (`ffmpeg ... (found on your PATH)`).
- [ ] Settings > Library lists ffmpeg and ffprobe with paths and versions. Rename `ffmpeg` away
      temporarily: the start-up notice should offer `sudo apt install ffmpeg`.
- [ ] Import a folder of music (File > Import Folder); the Explorer tab lists your home, Music and a
      **USB stick** (`/media/$USER/...`) if one is plugged in. Pin a folder.
- [ ] Preview: double-click a track. Sound comes out of the right device (Settings > Audio lists the
      devices; try PulseAudio / PipeWire and the buffer size). Scrub the waveform.
- [ ] Space, arrows and the Ctrl shortcuts (the Shortcuts window shows them as `Ctrl+...`).
- [ ] Print Labels: save a PDF; the CUPS print panel, if you have a printer **[not tried anywhere]**.
- [ ] Drag-to-change spin boxes and trackpad pinch in the label preview feel right.

## 3. Export (no drive needed)

- [ ] Plan an audio disc, open the burn window. It should say it will burn with cdrdao, and **Export…**
      works. Check the files, then from inside the export folder: `cdrdao show-toc disc.toc`.
- [ ] Plan an MP3 disc, **Export…**: a `disc.iso` next to the disc's folder. `isoinfo -d -i disc.iso` (or
      `xorriso -indev disc.iso -toc`) shows the volume name; mount it (`sudo mount -o loop,ro disc.iso /mnt`)
      and look at the names.
- [ ] An MP3 disc with a source that is not an MP3 (FLAC, M4A): the encode needs ffmpeg's LAME. Distro
      ffmpegs normally have it (`ffmpeg -encoders | grep mp3lame`); if not, that is a finding.

## 4. A test burn (laser off)

With a blank CD-R in the drive, **Burn… > Test only** (or `dubcrate burn N --yes --simulate`).

- [ ] The burn window shows the drive's name and "blank disc" and how much it holds (`disk-info`).
      **Record what `cdrdao disk-info --device /dev/sr0` actually printed**, and whether the numbers match.
- [ ] The log shows progress about every 10 percent, not one line per update.
- [ ] Ends with "Test burn finished", the disc is not marked as burned.
- [ ] Wrong cases give plain messages: no disc in the drive; a disc that is not blank; permission denied
      (take yourself out of the group); a speed the drive lacks.
- [ ] After a simulation cdrdao may want the disc reloaded before the real write (its README says so).

## 5. A real audio burn, played on a CDJ

Follow the hardware check in [burning.md](burning.md): a short disc first, 10x to 16x.

- [ ] The disc plays; **CD-TEXT** shows on the CDJ-1000MK3 (title and artist).
- [ ] Track boundaries are gapless (the `0` pregap in our TOC).
- [ ] Note the drive, the media and the speed in `burning.md`.

## 6. A real MP3 disc

- [ ] **Burn… on an MP3 disc** (the cdrdao path: ISO, then one `MODE1` data track).
- [ ] If that does not work, the alternative is `xorriso -as cdrecord -v dev=/dev/sr0 speed=10 disc.iso`
      on the exported image: record which one works on this drive and on the CDJ.
- [ ] The CDJ reads it, file order is right, long names show.

## 7. What to write down

Record each result in `cross-platform.md` (tag **[hardware DATE]**) and `burning.md`, and open a note for
each thing that did not work with the exact text on screen, `cdrdao`'s output and the log file
(`~/.local/state/Dubcrate/log/dubcrate.log`).

## Afterwards: packaging

Not started: an AppImage or Flatpak (Flatpak needs device access), or `uv tool install`. Decide with this
session's experience: see Phase 3 in [cross-platform.md](cross-platform.md).
