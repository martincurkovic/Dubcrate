"""Loads a track for preview and keeps the player, the strip and the track list in step."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dubcrate import settings
from dubcrate.analysis import Analysis, read_analysis, share_root
from dubcrate.audio.decode import DecodedAudio, decode_file
from dubcrate.audio.player import AudioPlayer
from dubcrate.beatjump import jump_time
from dubcrate.deck import Deck
from dubcrate.library import Library
from dubcrate.library.models import Track
from dubcrate.waveform import PREVIEW_COLUMNS, Waveform, compute_waveform

from .cue_cache import RekordboxCueCache
from .player_strip import PlayerStrip
from .tasks import run_async

# the arrow keys' jump for a track with no beat grid and no tempo
FALLBACK_JUMP_SECONDS = 2.0


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
        cues: RekordboxCueCache | None = None,
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
        self.deck = (
            Deck()
        )  # the cue point, loop and quantise of the track loaded (session-only)
        self._previewing = False  # CUE is held, playing from the cue point
        # rekordbox's cues come from its database, not the analysis files; the track list draws them too
        self.cues = cues or RekordboxCueCache(lib, self)
        self.cues.message.connect(self.message)
        self.cues.changed.connect(self._show_cues)

        self.player.position_changed.connect(strip.set_position)
        self.player.state_changed.connect(strip.set_playing)
        self.player.failed.connect(self.message)
        strip.play_toggled.connect(self.toggle)
        strip.beat_jump_requested.connect(self.beat_jump)
        strip.jump_requested.connect(self.jump_to)
        strip.cue_pressed.connect(self.cue_pressed)
        strip.cue_released.connect(self.cue_released)
        strip.quantise_toggled.connect(self.set_quantise)
        strip.auto_loop_requested.connect(self.auto_loop)
        strip.loop_size_changed.connect(self.loop_size)
        strip.loop_in_requested.connect(self.loop_in)
        strip.loop_out_requested.connect(self.loop_out)
        strip.reloop_requested.connect(self.reloop)
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
        self._strip.set_beat_jump(settings.get_beat_jump())

    def _on_volume(self, volume: float) -> None:
        self.player.set_volume(volume)
        settings.set_volume(volume)

    def toggle(self) -> None:
        if self.player.loaded:
            self.player.toggle()

    def jump_to(self, seconds: float) -> None:
        """Go to a time in the loaded track (a hot cue, a memory cue): playing or paused stays as it was."""
        if self.player.scratching or not self.player.loaded:
            return
        self.player.seek(max(0.0, min(seconds, self.player.duration)))

    # -- CUE, loops and quantise: the deck decides, the player does it -----------------------------

    def _show_session(self) -> None:
        self._strip.show_session(self.deck.markers())
        self._strip.set_loop_state(self.deck.loop_active)

    def _usable(self) -> bool:
        return (
            self.player.loaded
            and self._track is not None
            and not self.player.scratching
        )

    def cue_pressed(self) -> None:
        """CUE went down: back to the cue point and stop, or play from it while held, or set it here."""
        if not self._usable():
            return
        action = self.deck.cue_press(self.player.position, self.player.playing)
        if action == "return":
            self.player.pause()
            self.player.seek(self.deck.cue_point)
        elif action == "preview":
            self._previewing = True
            self.player.seek(self.deck.cue_point)
            self.player.play()
        self._show_session()

    def cue_released(self) -> None:
        """CUE came up: if it was previewing, back to the cue point and stop."""
        if self._previewing:
            self._previewing = False
            self.player.pause()
            self.player.seek(self.deck.cue_point)

    def set_quantise(self, on: bool) -> None:
        self.deck.quantise = on
        if on and self._track is not None and self.deck.beat_length() is None:
            self.message.emit(
                "This track has no beat grid or tempo, so there is no beat to snap to"
            )

    def auto_loop(self, beats: float) -> None:
        """The loop size button: leave a loop that is on, else loop this many beats from here."""
        if not self._usable():
            return
        if self.deck.loop_active:
            self.deck.exit_loop()
            self.player.clear_loop()
        else:
            self.deck.auto_beats = beats
            found = self.deck.auto_loop(self.player.position)
            if found is None:
                self.message.emit(
                    "This track has no beat grid or tempo, so use a manual loop (MA)"
                    if self.deck.beat_length() is None
                    else "That loop would run past the end of the track"
                )
            else:
                self.player.set_loop(*found)
        self._show_session()

    def loop_size(self, beats: float) -> None:
        """The size arrows: a new size, redrawing a loop that is on."""
        if not self._usable():
            return
        was_on = self.deck.loop_active
        found = self.deck.resize_auto(beats)
        if found is not None:
            self.player.set_loop(*found)
        elif was_on:
            self.message.emit("A loop that size would run past the end of the track")
        self._show_session()

    def loop_in(self) -> None:
        if not self._usable():
            return
        self.deck.mark_in(self.player.position)
        self.player.clear_loop()  # (a new IN leaves the loop that was on)
        self._show_session()

    def loop_out(self) -> None:
        if not self._usable():
            return
        found = self.deck.mark_out(self.player.position)
        if found is None:
            self.message.emit(
                "Press IN first"
                if self.deck.loop_in is None
                else "OUT has to come after IN"
            )
        else:
            self.player.set_loop(*found)
        self._show_session()

    def reloop(self) -> None:
        """RELOOP: leave a loop that is on, or go back to the start of the last one and loop it."""
        if not self._usable():
            return
        found = self.deck.reloop()
        if found is not None:
            self.player.seek(found[0])
            self.player.set_loop(*found)
        else:
            self.player.clear_loop()
        self._show_session()

    def beat_jump(self, direction: int) -> None:
        """Jump the set number of beats forward (`direction` 1) or back (-1), in phase.

        Uses the track's beat grid; without one, its tempo (rekordbox's, else the tag's); with
        neither, a fixed `FALLBACK_JUMP_SECONDS`.
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
            target = (
                position + beats * 60.0 / bpm
                if bpm
                else position + direction * FALLBACK_JUMP_SECONDS
            )
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

    def _show_cues(self) -> None:
        """Put the loaded track's rekordbox cues on the waveforms (the database may still be being read)."""
        if self._track is None or self._track is not self._requested:
            return  # nothing loaded, or another track is on its way
        if found := self.cues.cues_for(self._track):
            self._strip.show_cues(tuple(found))

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
        self._previewing = False
        self.deck.load(0.0, None, None)
        self.player.pause()
        self._strip.show_track(track)
        if track.missing:
            self._strip.show_error("File not found")
            self.message.emit(f"{track.title}: file not found")
            return
        # a file that is also in rekordbox (same path) uses rekordbox's waveform and beat grid
        analysed = self._lib.rekordbox_twin(track) or track
        root = self._analysis_root(analysed)

        def done(media: LoadedMedia) -> None:
            if serial != self._serial:
                return  # the user has since chosen something else
            self._track = track
            self._analysis = media.analysis
            analysis = media.analysis
            self.deck.load(
                media.audio.duration,
                analysis.beat_times if analysis else None,
                (analysis.bpm if analysis else None) or track.bpm,
            )
            self._strip.show_media(media.audio.duration, media.waveform, media.analysis)
            self.player.load(media.audio, autoplay=play)
            self._show_cues()
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
            lambda: load_media(track.path, root, analysed.analysis_path), done, failed
        )
