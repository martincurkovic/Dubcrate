"""Loads a track for preview and keeps the player, the strip and the track list in step."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dubcrate import settings
from dubcrate.analysis import Analysis, read_analysis, share_root
from dubcrate.audio.decode import DecodedAudio, decode_file
from dubcrate.audio.player import AudioPlayer
from dubcrate.beatjump import jump_time
from dubcrate.library import Library
from dubcrate.library.models import Track
from dubcrate.waveform import PREVIEW_COLUMNS, Waveform, compute_waveform

from .player_strip import PlayerStrip
from .tasks import run_async


@dataclass(frozen=True)
class LoadedMedia:
    audio: DecodedAudio
    waveform: Waveform
    analysis: Analysis | None


def load_media(
    path: str, analysis_root: Path | None, analysis_path: str | None
) -> LoadedMedia:
    """Decode a track and get its waveform: rekordbox's analysis if it has one, else computed.

    Touches no database, so it is safe to run on a worker thread.
    """
    audio = decode_file(path)
    analysis = (
        read_analysis(analysis_root, analysis_path, audio.duration)
        if analysis_root and analysis_path
        else None
    )
    if analysis is not None and analysis.waveform is not None:
        waveform = analysis.waveform
    else:
        waveform = compute_waveform(audio.mono())
    return LoadedMedia(audio, waveform, analysis)


class PreviewController(QObject):
    message = Signal(str)  # for the status bar
    loaded = Signal(object)  # the Track now loaded
    preview_ready = Signal(
        int, object
    )  # (track id, whole-track Waveform) for the track list

    def __init__(
        self,
        lib: Library,
        strip: PlayerStrip,
        player: AudioPlayer | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._lib = lib
        self._strip = strip
        self.player = player or AudioPlayer(parent=self)
        self._serial = 0  # a newer request makes an older one's result stale
        self._track: Track | None = None  # loaded and playable
        self._analysis: Analysis | None = None  # of the loaded track, for its beat grid
        self._requested: Track | None = (
            None  # the latest one asked for, which may still be loading
        )

        self.player.position_changed.connect(strip.set_position)
        self.player.state_changed.connect(strip.set_playing)
        self.player.failed.connect(self.message)
        strip.play_toggled.connect(self.toggle)
        strip.seek_requested.connect(self.player.seek)
        strip.scratch_started.connect(self.player.begin_scratch)
        strip.scratch_moved.connect(self.player.scratch_to)
        strip.scratch_ended.connect(self.player.end_scratch)
        strip.volume_changed.connect(self._on_volume)
        self.apply_settings()

    @property
    def track(self) -> Track | None:
        """The track that is loaded and ready."""
        return self._track

    @property
    def requested(self) -> Track | None:
        """The track most recently asked for, even if it is still loading: where Next and Previous step from."""
        return self._requested

    def apply_settings(self) -> None:
        """Pick up the saved output device, buffer size and volume."""
        self.player.configure(settings.get_audio_device(), settings.get_audio_buffer())
        volume = settings.get_volume()
        self.player.set_volume(volume)
        self._strip.set_volume(volume)

    def _on_volume(self, volume: float) -> None:
        self.player.set_volume(volume)
        settings.set_volume(volume)

    def toggle(self) -> None:
        if self.player.loaded:
            self.player.toggle()

    def beat_jump(self, direction: int) -> None:
        """Jump the set number of beats forward (`direction` 1) or back (-1), in phase.

        Uses the track's beat grid; without one, its tempo (rekordbox's, else the tag's).
        """
        if self.player.scratching:
            return
        if not self.player.loaded or self._track is None:
            # Beat jump acts on the track that is loaded, not the one that is selected.
            self.message.emit(
                "Double-click a track to load it, then use ← and → to beat jump"
            )
            return
        beats = settings.get_beat_jump() * direction
        position = self.player.position
        target = None
        if self._analysis is not None:
            target = jump_time(self._analysis.beat_times, position, beats)
        if target is None:
            bpm = (self._analysis.bpm if self._analysis else None) or self._track.bpm
            if not bpm:
                self.message.emit(
                    "No beat grid or tempo for this track, so it can't beat jump"
                )
                return
            target = position + beats * 60.0 / bpm
        self.player.seek(max(0.0, min(target, self.player.duration)))

    def shutdown(self) -> None:
        self._serial += 1
        self.player.shutdown()

    def _analysis_root(self, track: Track) -> Path | None:
        source = next((s for s in self._lib.sources() if s.id == track.source_id), None)
        return (
            share_root(source.path)
            if source is not None and source.kind == "rekordbox"
            else None
        )

    def load(self, track: Track, *, play: bool = True) -> None:
        """Load `track` for preview, and start it if `play`."""
        if (
            self._track is not None
            and self._track.id == track.id
            and self.player.loaded
        ):
            self._requested = track
            self.player.seek(0.0)  # already loaded: just start it again
            if play:
                self.player.play()
            return
        self._serial += 1
        serial = self._serial
        self._requested = track
        self._analysis = None
        self.player.pause()
        self._strip.show_track(track)
        if track.missing:
            self._strip.show_error("File not found")
            self.message.emit(f"{track.title}: file not found")
            return
        root = self._analysis_root(track)

        def done(media: LoadedMedia) -> None:
            if serial != self._serial:
                return  # the user has since chosen something else
            self._track = track
            self._analysis = media.analysis
            self._strip.show_media(media.audio.duration, media.waveform, media.analysis)
            self.player.load(media.audio, autoplay=play)
            self.preview_ready.emit(
                track.id, media.waveform.resampled(PREVIEW_COLUMNS, how="mean")
            )
            self.loaded.emit(track)

        def failed(text: str) -> None:
            if serial != self._serial:
                return
            self._strip.show_error(text)
            self.message.emit(f"Couldn't load {track.title}: {text}")

        run_async(
            lambda: load_media(track.path, root, track.analysis_path), done, failed
        )
