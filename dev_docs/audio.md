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
- **Crackles from the garbage collector [measured 2026-11-13, silent track through the real
  device, offscreen window; NOT heard by ear]:** the audio callback is a Python function
  (miniaudio's C thread calls `PlaybackDevice._data_callback` through cffi), so it needs the
  GIL, and anything on the GUI thread that holds the GIL for longer than one buffer lasts is an
  underrun. `spikes/audio_jitter.py` records when each callback fires. The player alone is
  steady at any size (worst gap 3.1 ms at 128 frames, 23.1 ms at 1024), but with the window
  playing, **a full garbage collection of 30 to 45 ms ran about every 6 s inside the zoomed
  waveform's paint** (the 3-band look builds thousands of `QPointF` objects a frame) and the
  callbacks were up to 56 ms apart: two buffers at 1024 (23 ms), hence the "very occasional"
  crackles. `ui/app.py: freeze_garbage_collector` (`gc.collect()` then `gc.freeze()` once the
  window is up) takes the start-up objects out of every later collection: worst gap 56 ms to 30
  ms and no late callbacks at 1024, clean at 512, nearly at 256 (2 late callbacks in 15 s).
  **Still open:** each waveform paint holds the GIL for about 8 ms in one C++ call, so 128
  frames is not safe (about 130 late callbacks in 15 s with the collector frozen). The next
  step would be painting the zoomed waveform from a cached image shifted a pixel row at a time
  (no per-frame Python objects, no long call); below that, only a callback that doesn't need the
  GIL would do, and `miniaudio` can't give one with our scratching.
- **Testing:** `render_block(state, frames)` is a pure function over `PlaybackState`;
  the player takes a `device_factory`, so tests drive a fake device and pull blocks
  by hand. `tests/conftest.py` makes every test use a fake device by default.
