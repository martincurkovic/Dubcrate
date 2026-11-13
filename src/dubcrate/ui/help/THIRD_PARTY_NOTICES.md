# Third-Party Notices

Dubcrate is free software, licensed under the **GNU General Public License, version 3 or later** (see the `LICENSE` file). It is built on the work of others, and this page names what it includes or depends on, and the licence each is used under. Each project's own copyright notice and full licence text is in its source or package, at the address given.

## Qt and PySide6

The window, menus, text and graphics are **Qt 6**, used through **Qt for Python (PySide6 and Shiboken)**. Copyright The Qt Company Ltd. and other contributors. Qt for Python is licensed under the **LGPL-3.0** (it is also available under the GPL-2.0 and GPL-3.0), and Dubcrate uses it under those terms. Qt is included as separate shared libraries. Source code: <https://code.qt.io> and <https://wiki.qt.io/Qt_for_Python>.

## Python, and the compiler that builds the app

The downloadable app contains the **Python** interpreter (PSF-2.0 licence, <https://www.python.org>) and is compiled with **Nuitka** (Apache-2.0, <https://nuitka.net>).

## Python packages

| Package | Licence | Notes |
| --- | --- | --- |
| **miniaudio** | MIT | Python bindings by Irmen de Jong. Includes David Reid's *miniaudio* and Sean Barrett's *stb_vorbis*. Plays the track previews. <https://github.com/irmen/pyminiaudio> |
| **NumPy** | BSD-3-Clause (parts under 0BSD, MIT, Zlib and CC0-1.0) | <https://numpy.org> |
| **pyrekordbox** | MIT | By Dylan Jones. Reads your rekordbox library, and never writes to it. <https://github.com/dylanljones/pyrekordbox> |
| **SQLAlchemy** | MIT | <https://www.sqlalchemy.org> |
| **sqlcipher3-wheels** | zlib/libpng | Contains **SQLCipher** (BSD-style licence, Zetetic LLC) and **OpenSSL 3** (Apache-2.0), which open rekordbox's encrypted database. <https://github.com/laggykiller/sqlcipher3> |
| **PyObjC** (pyobjc-core, pyobjc-framework-Cocoa, pyobjc-framework-DiscRecording) | MIT | The bridge to Apple's DiscRecording framework, which burns the CD. <https://github.com/ronaldoussoren/pyobjc> |
| **platformdirs** | MIT | <https://github.com/tox-dev/platformdirs> |
| **certifi** | MPL-2.0 | Certificates for the update check. <https://github.com/certifi/python-certifi> |
| **cffi** | MIT-0 | <https://cffi.readthedocs.io> |
| **pycparser** | BSD-3-Clause | <https://github.com/eliben/pycparser> |
| **construct** | MIT | <https://github.com/construct/construct> |
| **bidict** | MPL-2.0 | <https://github.com/jab/bidict> |
| **psutil** | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| **python-dateutil** | BSD-3-Clause | <https://github.com/dateutil/dateutil> |
| **packaging** | Apache-2.0 or BSD-2-Clause | <https://github.com/pypa/packaging> |
| **typing-extensions** | PSF-2.0 | <https://github.com/python/typing_extensions> |
| **tzdata** | Apache-2.0 | <https://github.com/python/tzdata> |

## Font

The **Macintosh System 6** theme uses **ChiKareGo2** by Giles Booth, a recreation of the Chicago 12 bitmap font, licensed under **Creative Commons Attribution (CC BY)**. It was obtained from [system.css](https://github.com/sakofchit/system.css). Apple's Chicago itself is not included.

## Shared code and design

The theme, the colour palettes, the icons and a few widgets (the volume knob and the drag-to-change spin boxes among them, from AKAISDS) are shared with the author's other apps: AKAISDS, JV Studio and akaiutil-GUI.

## rekordbox's analysis files

How rekordbox stores waveforms, beat grids, cue points and phrases was learned from Deep Symmetry's documentation of the format (<https://djl-analysis.deepsymmetry.org>) and checked against real files. No code was copied from it.

## Programs that come with Dubcrate, and are run, not linked

The app includes **ffmpeg** and **ffprobe** (FFmpeg 9.0.2, LGPL-2.1-or-later; <https://ffmpeg.org>), which read your tracks, convert them to CD quality and write MP3 files, and the **LAME** MP3 encoder (3.100, LGPL-2.0-or-later; <https://lame.sourceforge.io>), which is built into `ffmpeg`. They are built without `--enable-gpl` and without `--enable-nonfree`. Dubcrate starts them as separate programs and does not link them. They are in the `dubcrate/bin` folder inside the app, with their licence texts, their build configuration and the written offer of source (`licenses/`). The exact sources and the script that builds them (`scripts/build_ffmpeg_macos.sh`) are in the Dubcrate source repository, so you can rebuild them and put your own copy in that folder, or install ffmpeg on your `PATH`.

## Programs Dubcrate runs, but does not include

Dubcrate starts the optional **cdrdao** (GPL; <https://cdrdao.sourceforge.net>) as a separate program if you have it installed; it does not contain or link it. **DiscRecording** is part of macOS.

## Trademarks

rekordbox, Pioneer DJ, AlphaTheta and CDJ are trademarks of their owners. Apple, Macintosh, macOS and Chicago are trademarks of Apple Inc. Dubcrate is not affiliated with, or endorsed by, any of them.
