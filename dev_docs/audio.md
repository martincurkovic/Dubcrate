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
  **The GIL's switch interval [measured the same day; applied]:** with the collector frozen, 128
  frames was still not safe (about 300 late callbacks in 15 s). That was **not** one long call
  holding the GIL, as first guessed: Python hands the GIL to a waiting thread only every
  `sys.getswitchinterval()` (5 ms by default), longer than a 128-frame buffer (2.9 ms), so the
  audio thread waits whenever the GUI thread is running Python. `spikes/audio_jitter.py --switch-ms
  N` sets it. At 256 frames with a worker thread busy as well (`--worker`): 62 late callbacks in
  20 s at 5 ms, 5 at 1 ms, 0 at 0.5 and 0.2 ms. At 128 frames, same load: 2 late at 0.5 ms, **0 at
  0.2 ms** (worst gap 3.7 ms against a 3.0 ms period), 0 at 0.1 ms. The worker (a waveform, then a
  plain Python loop, repeated) got exactly as much done at every setting (93 rounds in 20 s), so
  the shorter interval cost it nothing measurable; a thread that does nothing but fight the GUI for
  the GIL would pay for the extra switches, which was not tried. `ui/app.py: tune_gil_for_audio`
  sets 0.2 ms at start-up (`GIL_SWITCH_INTERVAL`). **Not heard by ear.** The bigger change
  (painting the zoomed waveform from a cached image) is only worth doing if 128 still crackles on
  a real Mac: below that, only a callback that doesn't need the GIL would do, and `miniaudio`
  can't give one with our scratching.
- **Testing:** `render_block(state, frames)` is a pure function over `PlaybackState`;
  the player takes a `device_factory`, so tests drive a fake device and pull blocks
  by hand. `tests/conftest.py` makes every test use a fake device by default.
