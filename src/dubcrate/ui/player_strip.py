"""The player across the top: transport, what is loaded, and the two waveforms."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dubcrate.analysis import Analysis
from dubcrate.library.models import Track
from dubcrate.waveform import Waveform

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
    volume_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0
        style = QApplication.style()

        def button(icon: QStyle.StandardPixmap, tip: str) -> QToolButton:
            b = QToolButton()
            b.setIcon(style.standardIcon(icon))
            b.setToolTip(tip)
            b.setAutoRaise(False)
            return b

        self._play_icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        self._pause_icon = style.standardIcon(QStyle.StandardPixmap.SP_MediaPause)
        self.prev_button = button(
            QStyle.StandardPixmap.SP_MediaSkipBackward, "Previous track"
        )
        self.play_button = button(
            QStyle.StandardPixmap.SP_MediaPlay, "Play / pause (Space)"
        )
        self.next_button = button(
            QStyle.StandardPixmap.SP_MediaSkipForward, "Next track"
        )
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
        self.key.setMinimumWidth(34)
        self.bpm.setMinimumWidth(52)

        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Preview volume")
        self.volume.valueChanged.connect(lambda v: self.volume_changed.emit(v / 100))

        header = QHBoxLayout()
        header.setSpacing(6)
        for b in (self.prev_button, self.play_button, self.next_button):
            header.addWidget(b)
        header.addSpacing(8)
        header.addLayout(names, 1)
        header.addSpacing(8)
        for w in (self.elapsed, self.remaining, self.key, self.bpm, self.volume):
            header.addWidget(w)

        self.overview = OverviewView()
        self.overview.seek_requested.connect(self.seek_requested)
        self.detail = DetailView()
        self.detail.seek_requested.connect(self.seek_requested)

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

    def clear(self) -> None:
        self._duration = 0.0
        self.title.setText(IDLE_HINT)
        self.artist.setText("")
        self.key.setText("")
        self.bpm.setText("")
        self.set_position(0.0)
        self.overview.clear()
        self.detail.clear()
        self.set_playing(False)
        self._enable(False)

    def show_track(self, track: Track) -> None:
        """A track has been chosen: show who it is while it loads."""
        self._duration = track.duration
        self.title.setText(track.title)
        self.artist.setText(track.artist)
        self.key.setText(track.key)
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
        self.elapsed.setText(clock(seconds))
        self.remaining.setText(f"-{clock(max(0.0, self._duration - seconds))}")
        self.overview.set_position(seconds)
        self.detail.set_position(seconds)

    def set_playing(self, playing: bool) -> None:
        self.play_button.setIcon(self._pause_icon if playing else self._play_icon)
