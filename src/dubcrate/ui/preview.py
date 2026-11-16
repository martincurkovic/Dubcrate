"""Loads a track for preview and keeps the player, the strip and the track list in step."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from dubcrate import settings
from dubcrate.analysis import Analysis, read_analysis, share_root
from dubcrate.artwork import read_artwork
from dubcrate.audio.decode import DecodedAudio, decode_file
from dubcrate.audio.player import AudioPlayer
from dubcrate.beatjump import jump_time
from dubcrate.deck import Deck
from dubcrate.library import Library
from dubcrate.library.cue_edits import (
    MAX_MEMORY_CUES,
    SAME_LOOP_MS,
    is_memory_loop,
    memory_count,
    memory_list,
)
from dubcrate.library.models import Track
from dubcrate.waveform import PREVIEW_COLUMNS, Waveform, compute_waveform

from .cue_cache import RekordboxCueCache
from .player_strip import PlayerStrip
from .rekordbox_editor import RekordboxEditor
from .tasks import run_async

# the arrow keys' jump for a track with no beat grid and no tempo
FALLBACK_JUMP_SECONDS = 2.0
SAME_MEMORY_MS = 50  # a memory cue within this of another is the same one
NEAR_MEMORY_SECONDS = (
    1.0  # the ✕ by MEMORY deletes the memory cue this close to the playhead
)


@dataclass(frozen=True)
class LoadedMedia:
    audio: DecodedAudio
    waveform: Waveform
    analysis: Analysis | None
    artwork: bytes | None = None  # the embedded cover picture, if the file has one


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
    return LoadedMedia(audio, waveform, analysis, read_artwork(path))


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
        self.editor = RekordboxEditor(lib, self.cues, self)
        self.editor.message.connect(self.message)
        self.editor.settled.connect(self._show_cues)

        self.player.position_changed.connect(strip.set_position)
        self.player.state_changed.connect(strip.set_playing)
        self.player.failed.connect(self.message)
        strip.play_toggled.connect(self.toggle)
        strip.beat_jump_requested.connect(self.beat_jump)
        strip.jump_requested.connect(self.jump_to)
        strip.cue_pressed.connect(self.cue_pressed)
        strip.cue_released.connect(self.cue_released)
        strip.quantise_toggled.connect(self.set_quantise)
        strip.quantise_button.setChecked(settings.get_quantise())  # (as last left)
        strip.auto_loop_requested.connect(self.auto_loop)
        strip.loop_size_changed.connect(self.loop_size)
        strip.loop_in_requested.connect(self.loop_in)
        strip.loop_out_requested.connect(self.loop_out)
        strip.reloop_requested.connect(self.reloop)
        strip.memory_add_requested.connect(self.memory_add)
        strip.memory_delete_requested.connect(self.memory_delete_near)
        strip.memory_row_delete_requested.connect(self.memory_delete)
        strip.memory_move_requested.connect(self.memory_move)
        strip.memory_reset_requested.connect(self.memory_reset)
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
        elif abs(self.player.position - self.deck.cue_point) > 0.001:
            self.player.seek(
                self.deck.cue_point
            )  # (quantised: the playhead goes onto the beat it was set on)
        self._show_session()

    def cue_released(self) -> None:
        """CUE came up: if it was previewing, back to the cue point and stop."""
        if self._previewing:
            self._previewing = False
            self.player.pause()
            self.player.seek(self.deck.cue_point)

    def set_quantise(self, on: bool) -> None:
        self.deck.quantise = on
        settings.set_quantise(on)  # (remembered for the next start)
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
            # OUT is pressed with the playhead already at (or past) the loop's end, so it goes back to IN at
            # once, as a CDJ's does: otherwise it would play straight on out of the loop.
            self.player.set_loop(*found)
            self.player.seek(found[0])
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

    # -- memory cues: kept in Dubcrate, then saved into rekordbox if the user allowed it ----------------

    def _memory_cues(self, *, ask: bool = True) -> list[tuple[int, str]] | None:
        """The loaded track's memory cues now, (milliseconds, label), or None if they can't be changed yet
        (nothing loaded, or rekordbox's are still being read: an edit then would drop them). `ask`: the
        first-edit question about saving into rekordbox may be asked (not for a loop: loops stay in Dubcrate)."""
        if not self._usable():
            return None
        found = self.cues.cues_for(self._track)
        if found is None:
            self.message.emit(
                "rekordbox's cues are still being read: try again in a moment"
            )
            return None
        if ask:
            self.editor.allow(
                self._track
            )  # asks the first time a rekordbox track is edited
        return memory_list(found)

    def _memory_places(self) -> int:
        """How many of the ten memory places the loaded track uses now (cues and loops)."""
        found = self.cues.cues_for(self._track)
        return memory_count(found or ())

    def _loop_to_save(self) -> tuple[int, int] | None:
        """The loop that is on, in milliseconds, if there is one (what MEMORY saves)."""
        found = self.deck.loop
        if not self.deck.loop_active or found is None:
            return None
        return round(found[0] * 1000), round(found[1] * 1000)

    def _memory_loop_add(self, start: int, end: int) -> None:
        """MEMORY with a loop on: save the loop as a memory loop in Dubcrate, and, if loops are being written into
        rekordbox (`RekordboxEditor.WRITE_LOOPS`) and that is allowed, send it there too. The loop stays on."""
        if self._memory_cues(ask=False) is None:
            return
        # (asks the first time a rekordbox track is edited; a track outside rekordbox never goes there)
        goes = RekordboxEditor.WRITE_LOOPS and self.editor.allow(self._track)
        found = self.cues.cues_for(self._track) or ()
        if any(
            is_memory_loop(c)
            and c.end is not None
            and abs(c.time * 1000 - start) < SAME_LOOP_MS
            and abs(c.end * 1000 - end) < SAME_LOOP_MS
            for c in found
        ):
            self.message.emit("This loop is saved already")
        elif self._memory_places() >= MAX_MEMORY_CUES:
            self.message.emit(
                f"A track can have {MAX_MEMORY_CUES} memory cues and loops: delete one first"
            )
        else:
            mine = self._lib.memory_loops(self._track.path)
            self._lib.set_memory_loops(self._track.path, [*mine, (start, end, "")])
            self._show_cues()
            if goes:
                self.editor.saved(self._track)
            else:
                self.message.emit("Loop saved in Dubcrate (not in rekordbox)")

    def _delete_memory_loop(self, start: int) -> bool:
        """Delete the loop saved here that starts at `start` (ms). False: there is none."""
        mine = self._lib.memory_loops(self._track.path)
        kept = [m for m in mine if abs(m[0] - start) > 1]
        if len(kept) == len(mine):
            return False
        self._lib.set_memory_loops(self._track.path, kept)
        self._show_cues()
        return True

    def _save_memory(self, cues: list[tuple[int, str]]) -> None:
        self._lib.set_memory_cues(self._track.path, cues)
        self._show_cues()
        self.editor.saved(self._track)

    def memory_add(self) -> None:
        """MEMORY: a memory cue at the playhead (on the nearest beat with quantise on), or, with a loop on,
        a memory loop of that loop."""
        loop = self._loop_to_save()
        if loop is not None:
            return self._memory_loop_add(*loop)
        current = self._memory_cues()
        if current is None:
            return
        ms = round(self.deck.snap(self.player.position) * 1000)
        if any(abs(ms - other) < SAME_MEMORY_MS for other, _ in current):
            self.message.emit("There is a memory cue here already")
        elif self._memory_places() >= MAX_MEMORY_CUES:
            self.message.emit(
                f"A track can have {MAX_MEMORY_CUES} memory cues and loops: delete one first"
            )
        else:
            self._save_memory([*current, (ms, "")])

    def memory_delete(self, seconds: float) -> None:
        """Delete the memory cue (or a loop saved here) at `seconds` (a row's ✕)."""
        current = self._memory_cues(ask=False)
        if current is None:
            return
        ms = round(seconds * 1000)
        if not any(abs(m - ms) <= 1 for m, _ in current) and self._delete_memory_loop(
            ms
        ):
            return
        self.editor.allow(self._track)
        kept = [(m, label) for m, label in current if abs(m - ms) > 1]
        if len(kept) != len(current):
            self._save_memory(kept)

    def memory_delete_near(self) -> None:
        """The ✕ by MEMORY: delete the memory cue nearest the playhead, if one is within a second of it. A loop
        saved here that the playhead is inside, or starts within a second of it, goes first."""
        current = self._memory_cues(ask=False)
        if current is None:
            return
        here = self.player.position
        loops = self._lib.memory_loops(self._track.path)
        inside = [m for m in loops if m[0] / 1000 <= here <= m[1] / 1000]
        near = min(current, key=lambda c: abs(c[0] / 1000 - here), default=None)
        cue_gap = abs(near[0] / 1000 - here) if near else None
        loop_near = min(loops, key=lambda m: abs(m[0] / 1000 - here), default=None)
        loop_gap = abs(loop_near[0] / 1000 - here) if loop_near else None
        if inside and (cue_gap is None or cue_gap > NEAR_MEMORY_SECONDS):
            self._delete_memory_loop(inside[0][0])
        elif (
            loop_gap is not None
            and loop_gap <= NEAR_MEMORY_SECONDS
            and (cue_gap is None or loop_gap < cue_gap)
        ):
            self._delete_memory_loop(loop_near[0])
        elif near is None or cue_gap > NEAR_MEMORY_SECONDS:
            self.message.emit("No memory cue or loop near the playhead")
        else:
            self.editor.allow(self._track)
            self._save_memory([c for c in current if c is not near])

    def memory_move(self, seconds: float) -> None:
        """Move the memory cue at `seconds` to the playhead (on the nearest beat with quantise on)."""
        current = self._memory_cues()
        if current is None:
            return
        ms = round(seconds * 1000)
        target = round(self.deck.snap(self.player.position) * 1000)
        moving = [c for c in current if abs(c[0] - ms) <= 1]
        rest = [c for c in current if abs(c[0] - ms) > 1]
        if not moving:
            return
        if any(abs(target - other) < SAME_MEMORY_MS for other, _ in rest):
            self.message.emit("There is a memory cue here already")
            return
        self._save_memory([*rest, (target, moving[0][1])])

    def import_memory(self, items: list[tuple[Track, list[int]]]) -> int | None:
        """Add memory cues set on the player (the card's, `card_import`) to tracks: `items` are (track, new times in
        milliseconds). They are saved like any edit (Dubcrate's own table, then rekordbox if that is allowed; the
        question is asked once if it hasn't been). Returns how many were added, or None if rekordbox's cues are
        still being read (nothing is changed then, so no cue is dropped)."""
        current = []
        for track, _ in items:
            found = self.cues.cues_for(track)
            if found is None:
                self.message.emit(
                    "rekordbox's cues are still being read: try again in a moment"
                )
                return None
            current.append(memory_list(found))
        added = 0
        for (track, times), have in zip(items, current, strict=True):
            merged = list(have)
            for ms in times:
                if len(merged) >= MAX_MEMORY_CUES:
                    break
                if all(abs(ms - other) >= SAME_MEMORY_MS for other, _ in merged):
                    merged.append((ms, ""))
                    added += 1
            if merged == have:
                continue
            self.editor.allow(track)  # asks the first time a rekordbox track is edited
            self._lib.set_memory_cues(track.path, sorted(merged))
            self.editor.saved(track)
        self._show_cues()
        return added

    def memory_reset(self) -> None:
        """Forget the edits: rekordbox's memory cues again, and the loops saved here are gone."""
        if self._track is None:
            return
        self._lib.clear_memory_cues(self._track.path)
        self._lib.clear_memory_loops(self._track.path)
        self._show_cues()

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
        found = self.cues.cues_for(self._track)
        if found is None:
            return  # (rekordbox's are still being read)
        edited = self._lib.memory_cues(self._track.path) is not None or bool(
            self._lib.memory_loops(self._track.path)
        )
        cues = tuple(found)
        if not cues and not edited and self._analysis is not None:
            cues = tuple(
                self._analysis.cues
            )  # (the analysis file's own, when rekordbox has none for it)
        self._strip.show_cues(cues)
        self._strip.set_memory_edited(edited)

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
            self._strip.show_media(
                media.audio.duration, media.waveform, media.analysis, media.artwork
            )
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
