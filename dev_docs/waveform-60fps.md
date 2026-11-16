# 60 FPS waveform scrolling

**Status (2026-11-16): investigated, not built.** This is everything the investigation found, so the work can be picked up
later without redoing it. The short version is in `audio.md` ("60 FPS waveform scrolling: investigation") and `TODO.md`.

**The question.** rbxport and rekordbox both scroll the zoomed waveform at 60 FPS. Dubcrate's does about 33. Is 60 possible,
and is it a good idea?

**The answer.** Possible, and worth doing, but **not by changing the timer alone**: the default look (3-band) takes the whole
60 FPS budget just to paint one frame, so it has to get cheaper first. The RGB and Blue looks could go to 60 with only the
timer change. Nothing here has been tried on a real display, and audio dropouts at 60 FPS have not been measured.

## How the waveform is updated today

| Piece | Where | What it does |
|---|---|---|
| The tick | `src/dubcrate/audio/player.py`: `_TICK_MS = 30`, `AudioPlayer._timer`, `_on_tick` | A `QTimer` re-emits `position_changed(seconds)` every 30 ms while playing (so about 33 a second), and once more when paused or finished. |
| The position | `AudioPlayer.position` (`state.frame / sample rate`) | The audio thread's frame **as of its last callback**: it moves in buffer-sized steps (128 to 1024 frames: 2.9 to 23 ms). |
| The strip | `src/dubcrate/ui/player_strip.py`: `PlayerStrip.set_position`, `_show_position` | Updates the time labels, `overview.set_position` and `detail.set_position`. |
| The zoomed view | `src/dubcrate/ui/waveform_view.py`: `DetailView.set_position`, `paintEvent` | Calls `update()` if the position changed; `paintEvent` repaints the whole view every time. |
| The 3-band look (the default) | `DetailView._paint_bands` | Builds three lists of `QPointF` (top edge, then bottom edge back) for low, mid and high, and fills each with an **anti-aliased `drawPolygon`**. |
| The RGB and Blue looks | `DetailView._paint_columns`, `wave_style.render_columns`, `waveform.raster_columns` | Builds a numpy image, one pixel column per device pixel, and draws it with one `drawImage`. |
| Beat lines, cues, loop spans, bar counter | `_paint_beats`, `_paint_cues`, `_paint_bar_counter` | Small: about 0.5 ms together. |
| The overview | `OverviewView.set_position` | Costs next to nothing per tick (measured with the whole strip: 0.2 ms more than the detail view alone). |
| Which look | `settings.get_wave_style()` (`bands`, `rgb`, `blue`), `wave_style.current_style()` | **`bands` is the default.** `current_style()` reads QSettings on every paint (11 reads a frame, about 0.2 ms: worth caching). |
| Audio and the GIL | `ui/app.py`: `tune_gil_for_audio` (0.2 ms switch interval), `freeze_garbage_collector`; `audio.md` | The audio callback is Python, so anything on the GUI thread that holds the GIL for longer than a buffer lasts is an underrun. |

Scratching (dragging the zoomed waveform) is a separate path: the view follows the mouse events themselves, not the tick,
so it is not capped by it.

## What was measured

Offscreen (Qt's `offscreen` platform), at a **display scale of 2** (a Retina Mac), a **6-minute made-up track** with a beat
grid, cues and phrases, the **8 s default zoom**, the detail view **886 x 112 logical pixels** (1772 x 224 real). Reproduce with
`uv run python spikes/waveform_fps.py [--scale 2] [--seconds 360]`.

| One frame | Mean | p95 | Max | Frames a second it could manage |
|---|---|---|---|---|
| Detail view, **3-band** (default) | **16.0 ms** | 17.0 | 39.6 | 62 |
| Detail view, RGB | 3.3 ms | 3.5 | 3.9 | 299 |
| Detail view, Blue | 6.0 ms | 6.2 | 12.2 | 167 |
| Whole strip tick + event loop, 3-band | 16.2 ms | 17.2 | 39.4 | 62 |
| Whole strip tick + event loop, RGB | 3.6 ms | 3.9 | 4.3 | 276 |

**60 FPS is 16.7 ms a frame.** The 3-band look uses 16.0 of it with nothing left for the rest of the window (the max of 39.6
ms is a frame that would be dropped).

**GUI-thread duty cycle** (frame time x frames a second): the 3-band look today is 16.0 x 33 = **53%** of one core; at 60 FPS
it would be 16.0 x 60 = **96%**: saturated, so the window would feel slow and frames would run late. The RGB look would be 20%
at 60 FPS, the Blue 36%.

### Where the 3-band 16 ms go

A `cProfile` of 60 frames (0.93 s):

| | Time | Per frame | Calls |
|---|---|---|---|
| `QPainter.drawPolygon` | 0.654 s | **10.9 ms (70%)** | 762 (3 big fills a frame, and the beat-line triangles) |
| `_paint_bands` itself (Python: building the `QPointF` lists) | 0.175 s | 2.9 ms | 60 |
| `paintEvent` itself | 0.028 s | 0.5 ms | 60 |
| `drawLine` (beat lines) | 0.018 s | 0.3 ms | 1,220 |

Each big `drawPolygon` is **one C++ call of about 3.5 ms that holds the GIL for its whole length**. That is longer than a
128-frame audio buffer lasts (2.9 ms) and over half a 256-frame one (5.8 ms): the same mechanism as the crackles described
in `audio.md`, still present at a smaller size. (It is also what `TODO.md`'s "paint the zoomed waveform from a cached image
so the GUI holds the GIL for less time" was after.)

## The cheaper 3-band look (prototype, not in the app)

Build the 3-band image with numpy, as the RGB look already does, and find the colour of each pixel with **one palette
lookup** instead of filling polygons. Per pixel column the three heights are `bands * reach`; for each row, the colour is the
**last** band (low, then mid, then high: painter's order) whose half-height reaches that row. Code is in
`spikes/waveform_fps.py` (`lookup`), in short:

```python
ay = np.abs(np.arange(rows) + 0.5 - rows / 2)[
    :, None
]  # distance of each row from the middle
h = (bands * reach).T  # (3, columns)
idx = np.zeros((rows, cols), dtype=np.uint8)
for b in range(3):  # a later band covers an earlier one
    np.putmask(idx, ay <= h[b][None, :], b + 1)
pixels = palette[idx]  # palette: uint32 RGBA, entry 0 transparent
```

| Variant | Mean | Notes |
|---|---|---|
| Hard edges (one row per device pixel) | **2.2 ms** | Edges step by one device pixel. |
| Drawn at 2x the rows, then pairs averaged | **5.3 ms** | Smoother top and bottom edges. |
| Boolean-mask assignment (`img[mask] = colour`) | 9.5 ms | **Too slow: do not do it this way.** |
| The same with per-pixel coverage maths | 18.4 ms | **Too slow.** |

At 60 FPS the lookup version is 13% (hard) to 32% (smoothed) of one core, against 96% for the polygons. numpy releases the
GIL during its large array operations, so there should be no long GIL holds left except `drawImage` (**not measured**).

**What is not known about it:** how it *looks*. Polygon edges are slopes between samples; the raster steps by one device
pixel. At a scale of 2 that is probably invisible, at 1 it may be rougher. Compare a picture of each (same track, same
position) before it replaces the polygons. The colours must come from `wave_style` (the palette can differ from the theme's),
and the **System 6 (black and white) look** needs the same treatment (see `_BANDS` and `is_mono()` in `waveform_view.py`).

## Smoothness is not only the frame rate

The position the strip is given is the audio thread's frame at its **last callback**, so it moves in buffer-sized steps: 5.8
ms at 256 frames, 23 ms at 1024. At 60 FPS (a frame every 16.7 ms) with a big buffer, some frames would show the same
position as the one before and the scroll would judder.

**Fix: extrapolate.** In the audio callback record `time.monotonic()` with the frame it reached (inside `PlaybackState`, under
the lock `render_block` already takes). `AudioPlayer.position` then returns `frame + (now - callback_time) * sample_rate`
while playing, **capped at one buffer** past the frame. Things to get right:

- A **loop**: an extrapolated position past `loop_end` must wrap to `loop_start` (as `_take` does), not show beyond the loop.
- **Pause and the fade-out**, a **seek**, a **scratch**, and **end of track**: no extrapolation unless playing, and never past
  the track's length.
- The time to the speaker is later than the callback (output latency), so the waveform is already a little ahead of the sound;
  that is how it is today too. Extrapolation adds up to one buffer to that, so check by eye against the sound.

## The timer

- `_TICK_MS = 30` -> about 16 ms, with `QTimer.setTimerType(Qt.TimerType.PreciseTimer)` (the default coarse timer may be 5% off,
  and can round to the platform's tick).
- Better than a fixed 16: pick the interval from `QScreen.refreshRate()` and cap it at 60 (a 120 Hz Mac would then still tick
  at 60 unless 120 is wanted: not asked for).
- `update()` is coalesced by Qt; macOS flushes a widget repaint on the next event-loop pass, and the compositor limits it to
  the display's refresh. **Not measured:** vsync behaviour, tearing, or a ProMotion display. The offscreen platform has none.
- `PlayerStrip.set_position` also sets two labels and updates the overview each tick: fine at 60 (measured: tiny).

## Other things worth doing at the same time

- Cache `wave_style.current_style()` (QSettings read 11 times a frame).
- The beat-line triangles are 582 of the 762 `drawPolygon` calls: they could be one `drawPolygon` per bar line batch, or a
  cached pixmap of the cap shape. Small (0.5 ms), only after the big win.

## Options considered

| Option | Verdict |
|---|---|
| Change the timer only | **No for 3-band** (96% busy). Fine for RGB and Blue. |
| A numpy image per frame (the lookup above) | **Best fit:** same structure as the RGB look, 2 to 5 ms, no long GIL holds. |
| A pre-rendered strip of the whole track, scrolled with `drawPixmap` | Cheapest per frame, but big: at 8 s zoom, 110 px/s x 360 s x 2 (scale) = 79,000 px wide x 224 high x 4 bytes = 70 MB for one track, and it has to be redone whenever the zoom, look or theme changes. It would need tiles. More complex than the lookup, for little gain. |
| `drawPolygon` without anti-aliasing, or with half the points | **Not measured** (the shell was unavailable when this was written). A quick experiment: patch `QPainter.setRenderHint` to ignore `Antialiasing` in the spike and time `detail.render`; and try `step = 2` in `_paint_bands`. Worth ten minutes before building anything, though a non-AA polygon will look rougher than the raster would. |
| A GPU widget (`QOpenGLWidget`, `QQuickWidget`) | Heavy: a new dependency surface for Nuitka and a rewrite of the painting. Not warranted. |
| Qt Quick scene graph | Same: out of proportion. |

## The plan, in order

1. **The cheaper 3-band look.** A `render_bands` in `wave_style.py` beside `render_columns` (numpy, palette lookup), used by
   `DetailView._paint_bands`. Tests: its pixels for a few known heights (a band on top where it should be, the painter's order,
   mono palette); the `DetailView` paints in all three looks; the old polygon path may stay for the overview and track-list
   strip if they use it (check `OverviewView` and `track_table.render_preview`). **Show a before and after picture.**
2. **Re-measure** with `spikes/waveform_fps.py`: the 3-band look should be 2 to 5 ms (the numbers in this file are the
   baseline). Check the System 6 and light looks too.
3. **Position extrapolation** in `audio/player.py` (above), as a pure function with tests (like `render_block`: a fake clock,
   a loop, a seek, a pause).
4. **The tick** to about 16 ms, precise timer, interval from the screen's refresh rate.
5. **Measure the audio**: `spikes/audio_jitter.py` (see `audio.md`; `--switch-ms`, `--worker`) at buffers of 128 and 256 frames
   with the window playing at 60 FPS. **The acceptance test is no late callbacks** at the buffer sizes that are clean today
   (256 frames and up; 128 is "nearly").
6. **Look at it on a real display** (and a 120 Hz Mac if there is one), by eye and ear, in all four looks.

**Acceptance:** the zoomed waveform paints in under about 6 ms in every look at a scale of 2 (so the GUI thread is about a
third busy at 60 FPS), no extra late audio callbacks at 256 frames, no visible judder at the largest buffer size, and the 3-band
look is recognisably the same as before.

## What is not known

- How the raster 3-band look compares to the polygons by eye, at scale 1 and 2.
- The cost of the cheaper look **on the real GUI thread in the app** (offscreen has no compositor).
- Audio callback lateness at 60 FPS (the biggest unknown: the audio thread shares the GIL with the paint).
- Real display behaviour (vsync, 120 Hz).
- Battery and fan: a third of a core all the time the app plays.
- Whether people notice 33 against 60 at all on a 130 BPM track at 8 s: at 110 px a second the waveform moves 3.3 px a frame
  at 33 FPS and 1.8 px at 60. rekordbox and rbxport both do 60, which is the reason to.

## How to resume

```
uv run python spikes/waveform_fps.py                # the numbers above, on this machine
uv run python spikes/waveform_fps.py --scale 1      # a non-Retina display
uv run python spikes/audio_jitter.py --help          # the audio side (audio.md: "Crackles from the garbage collector")
```

Files to read first: `src/dubcrate/ui/waveform_view.py` (`DetailView`), `src/dubcrate/ui/wave_style.py` (`render_columns`,
`current_style`, the palette), `src/dubcrate/waveform.py` (`raster_columns`), `src/dubcrate/audio/player.py` (`_TICK_MS`,
`_on_tick`, `position`, `_take`), `src/dubcrate/ui/player_strip.py` (`set_position`), `dev_docs/audio.md`.
