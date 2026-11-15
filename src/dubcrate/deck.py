"""The preview deck's cue point, loops and quantise, pure (no Qt, no audio): what a CDJ's CUE, loop and Q
buttons decide, given where the playhead is and the track's beat grid.

**Session-only.** Nothing here is saved or written to rekordbox: a deck forgets all of it when another
track is loaded. The preview player (`ui/preview.py`) does what a `Deck` says to the audio engine.

- **CUE** (`cue_press`): while playing, it goes back to the cue point and stops; while stopped on the cue
  point, holding it plays from there (let go: back and stop); while stopped elsewhere, it sets the cue point
  to the playhead. The cue point starts at the beginning of the track.
- **Auto loop** (`auto_loop`): a loop of a chosen number of beats from the playhead; the size keys halve or
  double a loop that is on (`resize_auto`). It needs a tempo: the beat grid, else the BPM.
- **Manual loop**: IN (`mark_in`), OUT (`mark_out`, which starts the loop) and RELOOP (`reloop`, which leaves
  a loop that is on, or goes back into the last one).
- **Quantise** (`quantise`): the cue point, loop IN / OUT and an auto loop's start snap to the nearest beat
  (of the grid, else of the tempo counted from the track's start); without one they land where the playhead is.
"""

import numpy as np

from dubcrate.analysis import Cue

AUTO_SIZES = (0.5, 1, 2, 4, 8, 16, 32)  # beats an auto loop can be
DEFAULT_AUTO = 4.0
MIN_LOOP = 0.01  # seconds: a loop shorter than this is not one
SAME_PLACE = 0.02  # seconds: the playhead is "on" the cue point within this


def size_label(beats: float) -> str:
    """An auto loop's size as its button says it: `1/2`, `1`, `4`, `32`."""
    return "1/2" if beats == 0.5 else f"{beats:g}"


class Deck:
    """The cue point, the loop and the quantise switch for the track loaded."""

    def __init__(self) -> None:
        self.quantise = False
        self.auto_beats = DEFAULT_AUTO
        self.load(0.0, None, None)

    def load(self, duration: float, beat_times, bpm: float | None) -> None:
        """A new track: everything starts again (the cue point at the beginning, no loop)."""
        self.duration = duration
        self.beat_times = (
            np.zeros(0) if beat_times is None else np.asarray(beat_times, dtype=float)
        )
        self.bpm = bpm or None
        self.cue_point = 0.0
        self.cue_set = False
        self.loop_in: float | None = None
        self.loop_out: float | None = None
        self.loop_active = False

    # -- the beat grid ------------------------------------------------------------------

    def beat_length(self) -> float | None:
        """Seconds a beat lasts: the grid's own, else the BPM's; None with neither."""
        if len(self.beat_times) >= 2:
            return float(np.median(np.diff(self.beat_times)))
        return 60.0 / self.bpm if self.bpm else None

    def beat_of(self, seconds: float) -> float | None:
        """Where `seconds` is in beats (a fraction between beats; counted from the first beat, and past
        the grid's ends by its end intervals). None with no tempo."""
        times = self.beat_times
        if len(times) >= 2:
            if seconds <= times[0]:
                return (seconds - times[0]) / (times[1] - times[0])
            if seconds >= times[-1]:
                return (len(times) - 1) + (seconds - times[-1]) / (
                    times[-1] - times[-2]
                )
            return float(np.interp(seconds, times, np.arange(len(times))))
        length = self.beat_length()
        if length is None:
            return None
        origin = float(times[0]) if len(times) else 0.0
        return (seconds - origin) / length

    def time_of(self, beat: float) -> float | None:
        """The inverse of `beat_of`: the time of a (fractional) beat. None with no tempo."""
        times = self.beat_times
        if len(times) >= 2:
            if beat <= 0:
                return float(times[0] + beat * (times[1] - times[0]))
            if beat >= len(times) - 1:
                return float(
                    times[-1] + (beat - (len(times) - 1)) * (times[-1] - times[-2])
                )
            return float(np.interp(beat, np.arange(len(times)), times))
        length = self.beat_length()
        if length is None:
            return None
        origin = float(times[0]) if len(times) else 0.0
        return origin + beat * length

    def snap(self, seconds: float) -> float:
        """`seconds` moved to the nearest beat if quantise is on and there is a tempo, else as it is
        (always within the track)."""
        seconds = max(0.0, min(seconds, self.duration or seconds))
        if self.quantise and (beat := self.beat_of(seconds)) is not None:
            snapped = self.time_of(round(beat))
            if snapped is not None:
                return max(0.0, min(snapped, self.duration or snapped))
        return seconds

    # -- CUE ----------------------------------------------------------------------------

    def cue_press(self, position: float, playing: bool) -> str:
        """What pressing CUE does: `return` (back to the cue point, and stop), `preview` (play from it while
        held) or `set` (the cue point is now the playhead, on the beat if quantise is on)."""
        if playing:
            return "return"
        if abs(position - self.cue_point) < SAME_PLACE:
            return "preview"
        self.cue_point = self.snap(position)
        self.cue_set = True
        return "set"

    # -- loops ----------------------------------------------------------------------------

    @property
    def loop(self) -> tuple[float, float] | None:
        """The loop that is set (in, out), on or off."""
        if self.loop_in is not None and self.loop_out is not None:
            return self.loop_in, self.loop_out
        return None

    def auto_loop(self, position: float) -> tuple[float, float] | None:
        """Loop `auto_beats` beats from the playhead (from the nearest beat if quantise is on) and
        turn it on. None when the track has no tempo or the loop would run past its end."""
        start = self.snap(position)
        beat = self.beat_of(start)
        end = None if beat is None else self.time_of(beat + self.auto_beats)
        if (
            end is None
            or (self.duration and end > self.duration)
            or end - start < MIN_LOOP
        ):
            return None
        self.loop_in, self.loop_out, self.loop_active = start, end, True
        return start, end

    def resize_auto(self, beats: float) -> tuple[float, float] | None:
        """A new size for the auto loop: if one is on it is redrawn from the same start (None if that no
        longer fits the track, and the loop stays as it was); either way later auto loops use it."""
        if self.loop_active and self.loop_in is not None:
            beat = self.beat_of(self.loop_in)
            end = None if beat is None else self.time_of(beat + beats)
            if end is None or (self.duration and end > self.duration):
                return None
            self.loop_out = end
            self.auto_beats = beats
            return self.loop_in, end
        self.auto_beats = beats
        return None

    def mark_in(self, position: float) -> float:
        """Loop IN at the playhead. A loop that was on is left, and an OUT before the new IN is dropped."""
        self.loop_in = self.snap(position)
        self.loop_active = False
        if self.loop_out is not None and self.loop_out <= self.loop_in + MIN_LOOP:
            self.loop_out = None
        return self.loop_in

    def mark_out(self, position: float) -> tuple[float, float] | None:
        """Loop OUT at the playhead: the loop starts. None with no IN set, or if OUT is not after it."""
        if self.loop_in is None:
            return None
        end = self.snap(position)
        if end - self.loop_in < MIN_LOOP:
            return None
        self.loop_out, self.loop_active = end, True
        return self.loop_in, end

    def reloop(self) -> tuple[float, float] | None:
        """RELOOP: leave a loop that is on (None), else go back into the last one set (its in and out)."""
        if self.loop_active:
            self.loop_active = False
            return None
        if self.loop is None:
            return None
        self.loop_active = True
        return self.loop

    def exit_loop(self) -> None:
        self.loop_active = False

    # -- what the waveforms show ---------------------------------------------------------------

    def markers(self) -> tuple[Cue, ...]:
        """The cue point and the loop as cues for the waveforms to draw (kind `cue`, orange, and `loop`)."""
        found: list[Cue] = []
        if self.cue_set:
            found.append(Cue("cue", self.cue_point))
        if self.loop_active and self.loop is not None:
            found.append(Cue("loop", self.loop_in, end=self.loop_out))
        else:
            for edge in (self.loop_in, self.loop_out):
                if edge is not None:
                    found.append(Cue("cue", edge))
        return tuple(found)
