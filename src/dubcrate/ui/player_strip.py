"""The player across the top: transport, what is loaded, and the two waveforms."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dubcrate.analysis import Analysis
from dubcrate.library.models import Track
from dubcrate.waveform import Waveform

from . import key_style
from .gear_button import GearButton
from .knob import Knob
from .transport_button import TransportButton
from .waveform_view import DetailView, OverviewView

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        self.prev_button = TransportButton("previous")
        self.prev_button.setToolTip("Previous track")
        self.play_button = TransportButton("play")
        self.play_button.setToolTip("Play / pause (Space)")
        self.next_button = TransportButton("next")
        self.next_button.setToolTip("Next track")
        self.prev_button.clicked.connect(self.previous_requested)
        self.play_button.clicked.connect(self.play_toggled)
        self.next_button.clicked.connect(self.next_requested)

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
        )  # (buttons of different heights share a middle line)
        for b in (self.prev_button, self.play_button, self.next_button):
            header.addWidget(b, 0, centred)
        header.addSpacing(8)
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.overview)
        layout.addWidget(self.detail)
        self.clear()

    # -- what is shown ----------------------------------------------------

    def set_volume(self, volume: float) -> None:
        self.volume.blockSignals(True)
        self.volume.setValue(round(volume * 100))
        self.volume.blockSignals(False)

    def _show_key(self) -> None:
        self.key.setText(key_style.display(self._key))

    def clear(self) -> None:
        self._duration = 0.0
        self.title.setText(IDLE_HINT)
        self.artist.setText("")
        self._key = ""
        self._show_key()
        self.bpm.setText("")
        self.overview.clear()
        self.detail.clear()
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
        self.set_position(0.0)
        self.set_playing(False)
        self._enable(False)

    def show_media(
        self, duration: float, waveform: Waveform, analysis: Analysis | None
    ) -> None:
        """The track's audio and waveform are ready."""
        self._duration = duration
        analysis = analysis or Analysis()
        self.overview.set_track(duration, waveform, analysis.phrases, analysis.cues)
        self.detail.set_track(waveform, analysis.beat_times, analysis.beat_numbers)
        self.set_position(0.0)
        self._enable(True)

    def show_error(self, message: str) -> None:
        self.overview.clear()
        self.detail.clear(message)
        self._enable(False)

    def _enable(self, loaded: bool) -> None:
        self.play_button.setEnabled(loaded)

    def set_position(self, seconds: float) -> None:
        if self.detail.dragging:  # a hand is on the waveform: it decides the position
            return
        self._show_position(seconds)
        self.detail.set_position(seconds)

    def _show_position(self, seconds: float) -> None:
        self.elapsed.setText(clock(seconds))
        self.remaining.setText(f"-{clock(max(0.0, self._duration - seconds))}")
        self.overview.set_position(seconds)

    def _on_scratch_moved(self, seconds: float) -> None:
        self._show_position(seconds)  # the detail view has moved itself
        self.scratch_moved.emit(seconds)

    def set_playing(self, playing: bool) -> None:
        self.play_button.set_playing(playing)
