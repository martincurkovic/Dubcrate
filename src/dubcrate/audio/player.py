"""Play a decoded track through miniaudio, with play, pause, seek and volume.

The audio thread only ever calls `render_block`, a plain function over `PlaybackState`,
so the playback logic is testable without a sound card (tests drive it with a fake
device). The state is guarded by a lock: the audio callback holds it for the length of
one block, and the GUI thread only for a few assignments.

Starting, pausing and seeking are ramped across one block, so they don't click.

Lifecycle note (from AKAISDS): a miniaudio device's CoreAudio thread calls back INTO
Python. If the process starts to exit while one is still running, macOS kills the whole
process, so every player's device is stopped at interpreter exit.
"""

import atexit
import logging
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field

import miniaudio
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .decode import CHANNELS, DecodedAudio
from .devices import DEFAULT_BUFFER, SAMPLE_RATE, buffer_msec, resolve_output_device

log = logging.getLogger(__name__)

_LIVE_PLAYERS: "weakref.WeakSet[AudioPlayer]" = weakref.WeakSet()
_TICK_MS = 30  # how often the playhead is re-emitted: smooth enough, and costs nothing


def _stop_all_players() -> None:
    for player in list(_LIVE_PLAYERS):
        try:
            player.shutdown()
        except Exception:  # noqa: BLE001, S110 - at exit there is nothing useful left to do with a failure
            pass


atexit.register(_stop_all_players)


@dataclass
class PlaybackState:
    samples: np.ndarray | None = None  # int16 (frames, 2)
    frame: int = 0
    playing: bool = False
    gain: float = 0.0  # where the fade is now
    volume: float = 1.0
    finished: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def render_block(state: PlaybackState, frames: int) -> bytes:
    """The next `frames` frames of 16-bit stereo audio, advancing `state`. Silence when paused."""
    out = np.zeros((frames, CHANNELS), dtype=np.int16)
    with state.lock:
        if state.samples is None or (not state.playing and state.gain <= 0.0):
            return out.tobytes()
        total = len(state.samples)
        n = max(0, min(frames, total - state.frame))
        if n:
            target = state.volume if state.playing else 0.0
            chunk = state.samples[state.frame : state.frame + n].astype(np.float32)
            if state.gain == target:
                if target != 1.0:
                    chunk *= target
            else:  # ramp across the block: the click-free way to start, stop or change volume
                chunk *= np.linspace(state.gain, target, n, dtype=np.float32)[:, None]
            out[:n] = np.clip(chunk, -32768, 32767).astype(np.int16)
            state.gain = target
            state.frame += n
        if state.frame >= total:
            state.playing = False
            state.finished = True
            state.gain = 0.0
    return out.tobytes()


DeviceFactory = Callable[[int, int, object], object]


def _miniaudio_device(
    sample_rate: int, msec: int, device_id: object
) -> miniaudio.PlaybackDevice:
    return miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=CHANNELS,
        sample_rate=sample_rate,
        buffersize_msec=msec,
        device_id=device_id,
    )


class AudioPlayer(QObject):
    position_changed = Signal(float)  # seconds
    state_changed = Signal(bool)  # playing?
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self, device_factory: DeviceFactory | None = None, parent: QObject | None = None
    ):
        super().__init__(parent)
        self._factory = device_factory or _miniaudio_device
        self._state = PlaybackState()
        self._device = None
        self._device_id: str | None = None
        self._buffer_frames = DEFAULT_BUFFER
        self._was_playing = False
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        _LIVE_PLAYERS.add(self)

    # -- configuration ----------------------------------------------------

    def configure(self, device_id: str | None, buffer_frames: int) -> None:
        """Choose the output device and buffer size; takes effect immediately, even mid-track."""
        changed = (device_id, buffer_frames) != (self._device_id, self._buffer_frames)
        self._device_id, self._buffer_frames = device_id, buffer_frames
        if changed and self._device is not None:
            self._close_device()
            if self._state.samples is not None:
                self._open_device()

    # -- the loaded track -------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._state.samples is not None

    @property
    def playing(self) -> bool:
        return self._state.playing

    @property
    def duration(self) -> float:
        return (
            0.0
            if self._state.samples is None
            else len(self._state.samples) / SAMPLE_RATE
        )

    @property
    def position(self) -> float:
        return self._state.frame / SAMPLE_RATE

    @property
    def volume(self) -> float:
        return self._state.volume

    def load(self, audio: DecodedAudio, *, autoplay: bool = False) -> None:
        """Replace the current track. Playback starts from the beginning if `autoplay`."""
        self._set_playing(False)
        with self._state.lock:
            self._state.samples = audio.samples
            self._state.frame = 0
            self._state.gain = 0.0
            self._state.finished = False
        self.position_changed.emit(0.0)
        if autoplay:
            self.play()

    def unload(self) -> None:
        self._set_playing(False)
        with self._state.lock:
            self._state.samples = None
            self._state.frame = 0
            self._state.gain = 0.0
        self._close_device()
        self.position_changed.emit(0.0)

    # -- transport --------------------------------------------------------

    def play(self) -> None:
        if self._state.samples is None:
            return
        if self._device is None and not self._open_device():
            return
        with self._state.lock:
            if self._state.finished or self._state.frame >= len(self._state.samples):
                self._state.frame = 0
            self._state.finished = False
            self._state.playing = True
        self._timer.start()
        self._emit_state()

    def pause(self) -> None:
        self._set_playing(False)

    def toggle(self) -> None:
        self.pause() if self._state.playing else self.play()

    def seek(self, seconds: float) -> None:
        if self._state.samples is None:
            return
        with self._state.lock:
            frame = round(seconds * SAMPLE_RATE)
            self._state.frame = max(0, min(frame, len(self._state.samples)))
            self._state.gain = 0.0  # fade in from silence: no click at the jump
            self._state.finished = False
        self.position_changed.emit(self.position)

    def set_volume(self, volume: float) -> None:
        with self._state.lock:
            self._state.volume = max(0.0, min(1.0, volume))

    def shutdown(self) -> None:
        """Stop and release the device. Safe to call more than once."""
        self._timer.stop()
        with self._state.lock:
            self._state.playing = False
        self._close_device()

    # -- internals --------------------------------------------------------

    def _set_playing(self, playing: bool) -> None:
        with self._state.lock:
            self._state.playing = playing
        self._emit_state()

    def _emit_state(self) -> None:
        playing = self._state.playing
        if playing != self._was_playing:
            self._was_playing = playing
            self.state_changed.emit(playing)

    def _generator(self):
        required = yield b""  # primed with an empty yield: miniaudio's generator protocol
        while True:
            required = yield render_block(self._state, required)

    def _open_device(self) -> bool:
        entry = resolve_output_device(self._device_id)
        device_id = entry["id"] if entry is not None else None
        try:
            device = self._factory(
                SAMPLE_RATE, buffer_msec(self._buffer_frames), device_id
            )
            generator = self._generator()
            next(generator)
            device.start(generator)
        except Exception as exc:
            log.exception("couldn't open the audio output")
            self.failed.emit(f"Couldn't open the audio output: {exc}")
            return False
        self._device = device
        return True

    def _close_device(self) -> None:
        device, self._device = self._device, None
        if device is not None:
            try:
                device.stop()
                device.close()
            except Exception:
                log.exception("error closing the audio output")

    def _on_tick(self) -> None:
        state = self._state
        if state.finished and not state.playing:
            self._timer.stop()
            state.finished = False
            self._emit_state()
            self.position_changed.emit(self.position)
            self.finished.emit()
            return
        if not state.playing and state.gain <= 0.0:
            self._timer.stop()  # paused and the fade-out is done
        self._emit_state()
        self.position_changed.emit(self.position)
