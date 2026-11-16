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
- **60 FPS waveform scrolling: investigation [measured 2026-11-16, offscreen, `spikes/waveform_fps.py`; NOT tried on a real
  display or heard by ear].** **The full write-up, with the plan, is `dev_docs/waveform-60fps.md`.** rbxport and rekordbox scroll at 60 FPS; Dubcrate's zoomed waveform does about 33.
  - **Why 33:** the playhead is re-emitted by a `QTimer` every `_TICK_MS = 30` ms (`audio/player.py`), and each emit repaints the
    zoomed view (`PlayerStrip.set_position` -> `DetailView.update`). The overview costs next to nothing per tick.
  - **What a frame costs** (8 s window, 886 x 112 logical at 2x, 6-minute track with grid, cues and phrases): **3-band look
    (the default) 16.0 ms**, RGB look 3.3 ms, Blue look 6.0 ms. 60 FPS is 16.7 ms a frame, so **the default look has no budget
    left at all**: the GUI thread would be busy 96% of the time (53% today at 33 FPS), frames would run late and the window would
    feel slow.
  - **Where the 16 ms go:** three anti-aliased `drawPolygon` fills, **11 ms (70%)**, each one C++ call of about 3.5 ms; Python
    building the point lists, 3 ms. Those calls hold the GIL for their whole length, which is longer than a 128-frame audio buffer
    (2.9 ms) lasts and over half a 256-frame one: the same cause as the crackles above, still there at a smaller size. (This is
    also what the "paint the zoomed waveform from a cached image" idea in `TODO.md` was after.)
  - **A cheaper 3-band look exists** (prototype in the spike, not in the app): build the image with numpy, which pixel is which
    band found by one palette lookup, as the RGB look already does. **2.2 ms with hard edges, 5.3 ms with the top and bottom edges
    smoothed** (drawn at twice the rows and averaged). At 60 FPS that is 13 to 32% of one core, against 96% for the polygons, and
    numpy releases the GIL during its big array operations (not measured here). The look changes slightly (edges are one device
    pixel steps, not polygon slopes): an A/B picture is needed before it replaces the polygons, and the System 6 look (black and
    white) needs the same palette.
  - **Smoothness is not only the frame rate:** the position is the audio thread's frame at its last callback, so it moves in
    buffer-sized steps (5.8 ms at 256 frames, 23 ms at 1024). At 60 FPS with a big buffer some frames would repeat a position
    and the scroll would judder. The position should be extrapolated from the callback's time (`time.monotonic()`), capped at one
    buffer.
  - **Verdict:** possible, and worth doing, **in this order**: (1) the cheaper 3-band renderer (it also takes the GUI from 53% to
    about 17% of a core at today's 33 FPS, and removes the long GIL holds); (2) the tick from 30 ms to about 16 ms (a precise
    timer) with the extrapolated position; (3) measure the audio callbacks with `spikes/audio_jitter.py` at 128 and 256 frames
    with the window playing at 60 FPS before shipping; (4) look at it on a real display (a 120 Hz Mac too). The RGB and Blue looks
    could go to 60 FPS with only step 2. **Not measured:** a real display (vsync, 120 Hz), audio jitter at 60 FPS, battery.
- **Testing:** `render_block(state, frames)` is a pure function over `PlaybackState`;
  the player takes a `device_factory`, so tests drive a fake device and pull blocks
  by hand. `tests/conftest.py` makes every test use a fake device by default.
