"""The player across the top, laid out as rbxport's is: controls down the left, the two waveforms with the
cue buttons under them in the middle, and the memory cues, hot cues and info on the right.

- **Left:** previous / next track, beat jump back / forward with its size menu, CUE and play / pause.
- **Middle:** what is loaded (title, artist, time, key, tempo, the cog and the volume knob), the overview
  and the zoomed waveform (zoom + / RST / - over its left edge), then the row of cue buttons: hot cues A to H
  (a lit one jumps there), MEMORY with the previous / next memory cue, the loop controls and Q.
- **Right:** `player_side.SidePanel`.

A track is loaded by double-clicking it, or by **dragging it from a track list onto the player**
(`track_dropped`). **Everything about rekordbox's cues is read-only**: they are never written. CUE, the
loops and Q are the preview's own and **last only until another track is loaded** (`deck.Deck` decides
what they do, `PreviewController` does it, and `show_session` / `set_loop_state` show it here).
"""

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.analysis import Analysis, Cue
from dubcrate.deck import AUTO_SIZES, DEFAULT_AUTO, size_label
from dubcrate.library.models import Track
from dubcrate.waveform import Waveform

from . import key_style
from .gear_button import GearButton
from .knob import Knob
from .models import TRACK_MIME
from .player_controls import (
    RING_SIZE,
    ChoiceButton,
    CueRing,
    GroupDivider,
    PillButton,
    join,
)
from .player_side import HOT_SLOTS, SidePanel
from .transport_button import TransportButton
from .wave_style import hot_cue_color
from .waveform_view import DetailView, OverviewView

LEFT_WIDTH = 116
ROW_HEIGHT = 26  # every button in the cue row
SIDE_WIDTH = 250
READ_ONLY = "Cues are rekordbox's: Dubcrate shows them but never changes them."
KEPT_HERE = "It is kept in Dubcrate (and goes to the card); rekordbox's own cues are never changed."

IDLE_HINT = "Double-click a track to preview it"


def clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class PlayerStrip(QWidget):
    play_toggled = Signal()
    previous_requested = Signal()
    next_requested = Signal()
    seek_requested = Signal(float)
    scratch_started = Signal()
    scratch_moved = Signal(float)  # seconds
    scratch_ended = Signal()
    volume_changed = Signal(float)
    settings_requested = Signal()  # the cog was pressed
    beat_jump_requested = Signal(int)  # -1 back, 1 forward, by the size in the menu
    jump_requested = Signal(
        float
    )  # a hot cue, a memory cue or a list row: go to this time
    loop_requested = Signal(
        float, float
    )  # a loop row: go to its start with that loop on (start and end, seconds)
    track_dropped = Signal(int)  # a track id dragged from a track list onto the player
    cue_pressed = (
        Signal()
    )  # the CUE button went down / came up (holding it previews from the cue point)
    cue_released = Signal()
    quantise_toggled = Signal(bool)
    auto_loop_requested = Signal(
        float
    )  # the loop size button: a loop of this many beats, or leave the loop
    loop_size_changed = Signal(float)  # beats, from the size arrows
    loop_in_requested = Signal()
    loop_out_requested = Signal()
    reloop_requested = Signal()
    memory_add_requested = Signal()  # MEMORY: a memory cue at the playhead
    memory_delete_requested = (
        Signal()
    )  # the ✕ beside it: the memory cue at the playhead
    memory_row_delete_requested = Signal(
        float
    )  # a row's ✕ in the Memory list: that cue's time
    memory_move_requested = Signal(float)  # a row's "Move to Playhead": that cue's time
    memory_reset_requested = Signal()  # "Reset": rekordbox's memory cues again

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        self._position = 0.0
        self._cues: tuple[Cue, ...] = ()  # rekordbox's, for the lists and buttons
        self._markers: tuple[
            Cue, ...
        ] = ()  # the session's own (cue point, loop), drawn with them
        self._auto_index = AUTO_SIZES.index(DEFAULT_AUTO)
        self.setAcceptDrops(True)

        # -- the left column -----------------------------------------------------
        self.prev_button = TransportButton("previous")
        self.prev_button.setToolTip("Previous track")
        self.play_button = TransportButton("play", size=RING_SIZE)
        self.play_button.setToolTip("Play / pause (Space)")
        self.next_button = TransportButton("next")
        self.next_button.setToolTip("Next track")
        self.prev_button.clicked.connect(self.previous_requested)
        self.play_button.clicked.connect(self.play_toggled)
        self.next_button.clicked.connect(self.next_requested)
        self.jump_back_button = PillButton(
            "‹", width=50, height=30, bold=True, shape="tile", size_delta=8
        )
        self.jump_forward_button = PillButton(
            "›", width=50, height=30, bold=True, shape="tile", size_delta=8
        )
        self.jump_back_button.setToolTip("Beat jump back (←)")
        self.jump_forward_button.setToolTip("Beat jump forward (→)")
        self.jump_back_button.clicked.connect(lambda: self.beat_jump_requested.emit(-1))
        self.jump_forward_button.clicked.connect(
            lambda: self.beat_jump_requested.emit(1)
        )
        self.jump_combo = ChoiceButton(width=LEFT_WIDTH)  # (as wide as the column)
        for beats in settings.BEAT_JUMP_SIZES:
            self.jump_combo.addItem(settings.beat_jump_label(beats), beats)
        self.jump_combo.setToolTip("How far a beat jump goes")
        self.jump_combo.activated.connect(
            lambda _i: settings.set_beat_jump(self.jump_combo.currentData())
        )
        self.set_beat_jump(settings.get_beat_jump())
        self.cue_button = CueRing()
        self.cue_button.setToolTip(
            "CUE: while playing, back to the cue point and stop. Stopped on the cue point, hold it to "
            "play from there. Stopped anywhere else, it sets the cue point here (until another track is loaded)."
        )
        self.cue_button.setEnabled(False)
        self.cue_button.pressed.connect(self.cue_pressed)
        self.cue_button.released.connect(self.cue_released)

        skip = QHBoxLayout()
        skip.setSpacing(6)
        skip.addWidget(self.prev_button)
        skip.addWidget(self.next_button)
        jump = QHBoxLayout()
        jump.setSpacing(6)
        jump.addWidget(self.jump_back_button)
        jump.addWidget(self.jump_forward_button)
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addSpacing(
            10
        )  # (the group sits a little way down, level with the overview)
        left.addLayout(skip)
        left.addLayout(jump)
        left.addWidget(self.jump_combo)
        left.addStretch(1)
        left.addWidget(self.cue_button, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.play_button, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(
            1
        )  # (CUE and play float in the lower middle, not on the bottom edge)
        left_box = QWidget()
        left_box.setFixedWidth(LEFT_WIDTH)
        left_box.setLayout(left)
        left.setContentsMargins(0, 0, 0, 0)

        # -- the middle: what is loaded ---------------------------------------------
        self.title = QLabel(IDLE_HINT)
        self.title.setObjectName("panelTitle")
        self.title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.artist = QLabel("")
        self.artist.setObjectName("mutedLabel")
        self.artist.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(self.title)
        names.addWidget(self.artist)

        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        self.remaining.setObjectName("mutedLabel")
        self.key = QLabel("")
        self.bpm = QLabel("")
        for label in (self.elapsed, self.remaining, self.key, self.bpm):
            label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        self.elapsed.setMinimumWidth(40)
        self.remaining.setMinimumWidth(48)
        self.key.setMinimumWidth(56)
        self._key = ""  # as the source wrote it; shown in the notation Settings says
        key_style.notifier.changed.connect(self._show_key)
        self.bpm.setMinimumWidth(52)

        self.settings_button = GearButton()
        self.settings_button.setToolTip("Settings (⌘,)")
        self.settings_button.clicked.connect(self.settings_requested)

        self.volume = Knob()
        self.volume.setRange(0, 100)
        self.volume.setDefaultValue(80)  # a double-click goes back to this
        self.volume.setToolTip(
            "Preview volume: drag up or down (hold Shift to go slowly), double-click to reset"
        )
        self.volume.valueChanged.connect(lambda v: self.volume_changed.emit(v / 100))

        header = QHBoxLayout()
        header.setSpacing(6)
        centred = (
            Qt.AlignmentFlag.AlignVCenter
        )  # (widgets of different heights share a middle line)
        header.addLayout(names, 1)
        header.addSpacing(8)
        for w in (self.elapsed, self.remaining, self.key, self.bpm):
            header.addWidget(w, 0, centred)
        header.addSpacing(4)
        header.addWidget(
            self.settings_button, 0, centred
        )  # the cog, left of the volume knob
        header.addWidget(self.volume, 0, centred)

        self.overview = OverviewView()
        self.overview.seek_requested.connect(self.seek_requested)
        self.detail = DetailView()
        self.detail.scratch_started.connect(self.scratch_started)
        self.detail.scratch_moved.connect(self._on_scratch_moved)
        self.detail.scratch_ended.connect(self.scratch_ended)

        # -- the cue row: hot cues, memory cues, loops, quantise ---------------------------
        # Related buttons are joined into groups (one rounded outline, a line between them), and the groups are
        # told apart by a thin divider: [A..H] | [MEMORY ◀ ▶ ✕] | [AU MA] [‹ 4 ›  or  IN OUT RELOOP] ... [Q]
        self.hot_buttons: list[PillButton] = []
        self._hot_times: dict[int, float] = {}
        hot_row = QHBoxLayout()
        hot_row.setSpacing(4)
        hot_row.setContentsMargins(0, 0, 0, 0)
        for slot in range(1, HOT_SLOTS + 1):
            button = PillButton(
                "ABCDEFGH"[slot - 1],
                color=hot_cue_color(slot),
                width=28,
                height=ROW_HEIGHT,
                bold=True,
            )
            button.setEnabled(False)
            button.setToolTip(f"Hot cue {'ABCDEFGH'[slot - 1]}: not set in rekordbox")
            button.clicked.connect(lambda _c=False, n=slot: self._jump_to_hot(n))
            self.hot_buttons.append(button)
            hot_row.addWidget(button)

        self.memory_button = PillButton(
            "MEMORY", width=70, height=ROW_HEIGHT, bold=True
        )
        self.memory_button.setEnabled(False)
        self.memory_button.setToolTip(
            "Add a memory cue at the playhead (on the nearest beat with Q on); with a loop on, "
            "save that loop as a memory loop. " + KEPT_HERE
        )
        self.memory_button.clicked.connect(self.memory_add_requested)
        self.memory_back_button = PillButton("◀", width=30, height=ROW_HEIGHT)
        self.memory_next_button = PillButton("▶", width=30, height=ROW_HEIGHT)
        self.memory_delete_button = PillButton("✕", width=30, height=ROW_HEIGHT)
        self.memory_delete_button.setEnabled(False)
        self.memory_delete_button.setToolTip(
            "Delete the memory cue (or a loop saved here) at the playhead. " + KEPT_HERE
        )
        self.memory_delete_button.clicked.connect(self.memory_delete_requested)
        self.memory_back_button.setToolTip("Jump to the memory cue before this point")
        self.memory_next_button.setToolTip("Jump to the memory cue after this point")
        self.memory_back_button.clicked.connect(lambda: self._jump_memory(-1))
        self.memory_next_button.clicked.connect(lambda: self._jump_memory(1))

        self.auto_button = PillButton("AU", checkable=True, width=36, height=ROW_HEIGHT, bold=True)  # fmt: skip
        self.manual_button = PillButton("MA", checkable=True, width=36, height=ROW_HEIGHT, bold=True)  # fmt: skip
        self.auto_button.setChecked(True)
        self.auto_button.clicked.connect(lambda: self._loop_mode(0))
        self.manual_button.clicked.connect(lambda: self._loop_mode(1))
        self.auto_button.setToolTip("Automatic loop: a set number of beats")
        self.manual_button.setToolTip("Manual loop: set the in and out points")

        self.loop_size_back = PillButton(
            "‹", width=30, height=ROW_HEIGHT, bold=True, size_delta=5
        )
        self.loop_size = PillButton(
            size_label(DEFAULT_AUTO),
            checkable=True,
            width=46,
            height=ROW_HEIGHT,
            bold=True,
        )
        self.loop_size_forward = PillButton(
            "›", width=30, height=ROW_HEIGHT, bold=True, size_delta=5
        )
        self.loop_size_back.clicked.connect(lambda: self._step_loop_size(-1))
        self.loop_size_forward.clicked.connect(lambda: self._step_loop_size(1))
        self.loop_size.clicked.connect(
            lambda: self.auto_loop_requested.emit(AUTO_SIZES[self._auto_index])
        )
        self.loop_in = PillButton("IN", width=48, height=ROW_HEIGHT)
        self.loop_out = PillButton("OUT", width=48, height=ROW_HEIGHT)
        self.reloop = PillButton("RELOOP", checkable=True, width=68, height=ROW_HEIGHT)
        self.loop_in.clicked.connect(self.loop_in_requested)
        self.loop_out.clicked.connect(self.loop_out_requested)
        self.reloop.clicked.connect(self.reloop_requested)
        for w, tip in (
            (self.loop_size_back, "A smaller loop (half as many beats)"),
            (
                self.loop_size,
                "Loop this many beats from here (press again to leave the loop)",
            ),
            (self.loop_size_forward, "A bigger loop (twice as many beats)"),
            (self.loop_in, "Loop IN: the loop starts here"),
            (self.loop_out, "Loop OUT: the loop ends here, and starts"),
            (self.reloop, "RELOOP: leave the loop, or go back into the last one"),
        ):
            w.setEnabled(False)
            w.setToolTip(tip)
        # only the group for the chosen mode is shown, so the row never keeps room for the wider one
        self.loop_auto_group = QWidget()
        self.loop_auto_group.setLayout(
            join([self.loop_size_back, self.loop_size, self.loop_size_forward])
        )
        self.loop_manual_group = QWidget()
        self.loop_manual_group.setLayout(
            join([self.loop_in, self.loop_out, self.reloop])
        )
        self.loop_manual_group.hide()

        self.quantise_button = PillButton("Q", checkable=True, width=36, height=ROW_HEIGHT, bold=True)  # fmt: skip
        self.quantise_button.setEnabled(False)
        self.quantise_button.setToolTip(
            "Quantise: a cue point or loop edge lands on the nearest beat (needs a beat grid or a tempo)"
        )
        self.quantise_button.toggled.connect(self.quantise_toggled)

        cue_row = QHBoxLayout()
        cue_row.setSpacing(0)
        cue_row.setContentsMargins(0, 0, 0, 0)
        cue_row.addLayout(hot_row)
        cue_row.addWidget(GroupDivider(height=ROW_HEIGHT - 6))
        cue_row.addLayout(
            join(
                [
                    self.memory_button,
                    self.memory_back_button,
                    self.memory_next_button,
                    self.memory_delete_button,
                ]
            )
        )
        cue_row.addWidget(GroupDivider(height=ROW_HEIGHT - 6))
        cue_row.addLayout(join([self.auto_button, self.manual_button]))
        cue_row.addSpacing(8)
        cue_row.addWidget(self.loop_auto_group)
        cue_row.addWidget(self.loop_manual_group)
        cue_row.addStretch(1)
        cue_row.addWidget(self.quantise_button)

        middle = QVBoxLayout()
        middle.setSpacing(6)
        middle.setContentsMargins(0, 0, 0, 0)
        middle.addLayout(header)
        middle.addWidget(self.overview)
        middle.addWidget(self.detail)
        middle.addLayout(cue_row)

        # -- the right: memory cues, hot cues, info -----------------------------------------
        self.side = SidePanel()
        self.side.setFixedWidth(SIDE_WIDTH)
        self.side.jump_requested.connect(self.jump_requested)
        self.side.loop_requested.connect(self.loop_requested)
        self.side.memory_delete_requested.connect(self.memory_row_delete_requested)
        self.side.memory_move_requested.connect(self.memory_move_requested)
        self.side.memory_reset_requested.connect(self.memory_reset_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(left_box)
        layout.addLayout(middle, 1)
        layout.addWidget(self.side)
        self.clear()

    # -- dropping a track on the player ------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(TRACK_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(TRACK_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        try:
            ids = json.loads(bytes(event.mimeData().data(TRACK_MIME)).decode())
        except (ValueError, TypeError):
            return
        if ids:
            event.acceptProposedAction()
            self.track_dropped.emit(int(ids[0]))

    # -- the beat jump menu ---------------------------------------------------

    def set_beat_jump(self, beats: int) -> None:
        """Show the saved beat jump size (without saving it again)."""
        self.jump_combo.setCurrentIndex(max(0, self.jump_combo.findData(beats)))

    # -- cues ---------------------------------------------------------------------

    def _loop_mode(self, which: int) -> None:
        self.auto_button.setChecked(which == 0)
        self.manual_button.setChecked(which == 1)
        self.loop_auto_group.setVisible(which == 0)
        self.loop_manual_group.setVisible(which == 1)

    def _step_loop_size(self, direction: int) -> None:
        """The size arrows: the next smaller (-1) or bigger (1) auto loop."""
        index = max(0, min(len(AUTO_SIZES) - 1, self._auto_index + direction))
        if index == self._auto_index:
            return
        self._auto_index = index
        self.loop_size.setText(size_label(AUTO_SIZES[index]))
        self.loop_size_changed.emit(AUTO_SIZES[index])

    def set_memory_edited(self, edited: bool) -> None:
        """Say that the track's memory cues are the ones edited in Dubcrate (not rekordbox's), with a Reset."""
        self.side.set_edited(edited)

    def set_loop_state(self, active: bool) -> None:
        """Show whether a loop is on (the size button and RELOOP light up)."""
        self.loop_size.setChecked(active)
        self.reloop.setChecked(active)

    def show_session(self, markers: tuple[Cue, ...]) -> None:
        """The session's own cue point and loop, drawn on the waveforms beside rekordbox's cues."""
        self._markers = tuple(markers)
        self._draw_cues()

    def _draw_cues(self) -> None:
        both = (*self._cues, *self._markers)
        self.overview.set_cues(both)
        self.detail.set_cues(both)

    def _jump_to_hot(self, slot: int) -> None:
        if slot in self._hot_times:
            self.jump_requested.emit(self._hot_times[slot])

    def _memory_times(self) -> list[float]:
        return sorted(c.time for c in self._cues if c.slot is None)

    def _jump_memory(self, direction: int) -> None:
        """To the nearest memory cue before (-1) or after (1) the playhead."""
        times = self._memory_times()
        if direction < 0:
            earlier = [t for t in times if t < self._position - 0.1]
            target = earlier[-1] if earlier else None
        else:
            later = [t for t in times if t > self._position + 0.1]
            target = later[0] if later else None
        if target is not None:
            self.jump_requested.emit(target)

    def _set_cues(self, cues: tuple[Cue, ...]) -> None:
        self._cues = tuple(cues)
        self._hot_times = {c.slot: c.time for c in self._cues if c.slot}
        for slot, button in enumerate(self.hot_buttons, start=1):
            has = slot in self._hot_times
            button.setEnabled(has)
            letter = "ABCDEFGH"[slot - 1]
            button.setToolTip(
                f"Hot cue {letter}: jump there"
                if has
                else f"Hot cue {letter}: not set in rekordbox"
            )
        has_memory = bool(self._memory_times())
        self.memory_back_button.setEnabled(has_memory)
        self.memory_next_button.setEnabled(has_memory)
        self.side.set_cues(self._cues)

    # -- what is shown ----------------------------------------------------

    def set_volume(self, volume: float) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(round(volume * 100))
        self.volume.blockSignals(False)

    def _show_key(self) -> None:
        self.key.setText(key_style.display(self._key))

    def clear(self) -> None:
        self._duration = 0.0
        self._position = 0.0
        self.title.setText(IDLE_HINT)
        self.artist.setText("")
        self._key = ""
        self._show_key()
        self.bpm.setText("")
        self.overview.clear()
        self.detail.clear()
        self.side.set_track(None)
        self._markers = ()
        self._set_cues(())
        self.set_loop_state(False)
        self.set_position(0.0)
        self.set_playing(False)
        self._enable(False)

    def show_track(self, track: Track) -> None:
        """A track has been chosen: show who it is while it loads."""
        self._duration = track.duration
        self.title.setText(track.title)
        self.artist.setText(track.artist)
        self._key = track.key
        self._show_key()
        self.bpm.setText(f"{track.bpm:.2f}" if track.bpm else "")
        self.overview.clear()
        self.detail.clear("Loading…")
        self.side.set_track(track)
        self._markers = ()
        self._set_cues(())
        self.set_loop_state(False)
        self.set_position(0.0)
        self.set_playing(False)
        self._enable(False)

    def show_media(
        self,
        duration: float,
        waveform: Waveform,
        analysis: Analysis | None,
        artwork: bytes | None = None,
    ) -> None:
        """The track's audio and waveform are ready (and its embedded cover picture, if it has one)."""
        self.side.set_artwork(artwork)
        self._duration = duration
        analysis = analysis or Analysis()
        both = (*analysis.cues, *self._markers)
        self.overview.set_track(duration, waveform, analysis.phrases, both)
        self.detail.set_track(
            waveform, analysis.beat_times, analysis.beat_numbers, cues=both
        )
        self._set_cues(analysis.cues)
        self.set_position(0.0)
        self._enable(True)

    def show_cues(self, cues: tuple[Cue, ...]) -> None:
        """The cues of the track on show, when they are found after it loaded."""
        self._set_cues(cues)
        self._draw_cues()

    def show_error(self, message: str) -> None:
        self.overview.clear()
        self.detail.clear(message)
        self._enable(False)

    def _enable(self, loaded: bool) -> None:
        self.play_button.setEnabled(loaded)
        for w in (
            self.memory_button,
            self.memory_delete_button,
            self.jump_back_button,
            self.jump_forward_button,
            self.cue_button,
            self.quantise_button,
            self.loop_size_back,
            self.loop_size,
            self.loop_size_forward,
            self.loop_in,
            self.loop_out,
            self.reloop,
        ):
            w.setEnabled(loaded)

    def set_position(self, seconds: float) -> None:
        if self.detail.dragging:  # a hand is on the waveform: it decides the position
            return
        self._show_position(seconds)
        self.detail.set_position(seconds)

    def _show_position(self, seconds: float) -> None:
        self._position = seconds
        self.elapsed.setText(clock(seconds))
        self.remaining.setText(f"-{clock(max(0.0, self._duration - seconds))}")
        self.overview.set_position(seconds)

    def _on_scratch_moved(self, seconds: float) -> None:
        self._show_position(seconds)  # the detail view has moved itself
        self.scratch_moved.emit(seconds)

    def set_playing(self, playing: bool) -> None:
        self.play_button.set_playing(playing)
