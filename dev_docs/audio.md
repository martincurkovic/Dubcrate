# Audio preview: what we found

Tagged like the other notes: **[measured DATE]**, **[tests]**, **[AKAISDS]** (carried
over from that app's own findings).

- **Library:** `miniaudio` 1.73 (cffi binding), as in AKAISDS. Its player is the model
  for `audio/player.py`: a `PlaybackDevice` fed by a generator, started with
  `next(generator)` first (the "primed empty yield"), then `device.start(generator)`.
  Buffer size is given in **milliseconds** (`buffersize_msec`), so the settings store
  frames and convert (`audio/devices.buffer_msec`).
- **Decoding [measured 2026-11-11]:** miniaudio decodes MP3 and WAV, and **fails on
  M4A** (`DecodeError`). So `audio/decode.py` falls back to ffmpeg. AIFF is expected to
  need the fallback too but has not been tried on its own. Everything is converted to
  44.1 kHz stereo int16 (the CD format), whatever the source.
- **Cost [measured 2026-11-11]:** decoding a 295 s MP3 took 0.17 s and 52 MB (about
  10 MB per minute); computing its waveform took 0.47 s. One track is held in memory at
  a time, which makes seeking instant.
- **Device ids:** `device["id"]` is a cffi union; its bytes as hex are stable across
  enumerations and are what is saved in the settings. A saved device that has gone
  falls back to the system default. [tests]
- **Real device [measured 2026-11-11]:** a silent two-second track opened the default
  output, advanced the position correctly (0.74 s after 0.8 s of wall time, so about
  50 ms of start-up latency), stopped on pause, and shut down cleanly. Audible
  playback through the window has **not** been checked by ear.
- **Quiet start, stop and seek:** every start, pause and seek ramps the gain across
  one block, so there are no clicks. [tests] (`render_block`)
- **Exit crash [AKAISDS]:** a running miniaudio device's CoreAudio thread calls back
  into Python; if the process starts to exit while one runs, macOS kills it. Every
  `AudioPlayer` is registered for an `atexit` shutdown, and `closeEvent` shuts it
  down. Keep both.
- **Testing:** `render_block(state, frames)` is a pure function over `PlaybackState`;
  the player takes a `device_factory`, so tests drive a fake device and pull blocks
  by hand. `tests/conftest.py` makes every test use a fake device by default.
