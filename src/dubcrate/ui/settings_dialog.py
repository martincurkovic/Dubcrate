"""Settings: where audio goes, how big the buffer is, and how the app looks."""

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dubcrate import settings
from dubcrate.audio import devices
from dubcrate.audio.decode import DecodedAudio
from dubcrate.audio.devices import BUFFER_SIZES, SAMPLE_RATE, buffer_msec
from dubcrate.audio.player import AudioPlayer

from . import theme, wave_style

TONE_SECONDS = 0.8
TONE_LEVEL = 0.25  # a test tone should never startle anyone


def make_test_tone() -> DecodedAudio:
    """A short 440 Hz tone with soft edges, as CD-format audio."""
    frames = int(SAMPLE_RATE * TONE_SECONDS)
    t = np.arange(frames) / SAMPLE_RATE
    envelope = np.minimum(
        1.0, np.minimum(t, TONE_SECONDS - t) / 0.05
    )  # 50 ms fades: no clicks
    wave = (np.sin(2 * np.pi * 440 * t) * envelope * TONE_LEVEL * 32767).astype(
        np.int16
    )
    return DecodedAudio(np.repeat(wave[:, None], 2, axis=1))


class SettingsDialog(QDialog):
    audio_changed = Signal()
    theme_chosen = Signal(str)

    def __init__(self, parent=None, device_factory=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self._device_factory = device_factory  # tests: a stand-in for the sound card
        self._tone_player: AudioPlayer | None = None

        tabs = QTabWidget()
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._discs_tab(), "Discs")
        tabs.addTab(self._burning_tab(), "Burning")
        tabs.addTab(self._library_tab(), "Library")
        tabs.addTab(self._appearance_tab(), "Appearance")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # -- audio ------------------------------------------------------------

    def _audio_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        note = QLabel(
            "Where track previews play, and how much audio is buffered. Changes apply straight away."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        form.addRow(note)

        self.device_combo = QComboBox()
        self.device_combo.addItem("(System Default)", None)
        try:
            found = devices.list_output_devices()
        except Exception:  # noqa: BLE001 - no audio backend at all: the default is all we can offer
            found = []
        for device in found:
            self.device_combo.addItem(device["name"], devices.device_id_string(device))
        saved = settings.get_audio_device()
        index = self.device_combo.findData(saved)
        if saved and index < 0:  # chosen once, not connected now
            self.device_combo.addItem("Saved device (not connected)", saved)
            index = self.device_combo.count() - 1
        self.device_combo.setCurrentIndex(max(0, index))
        self.device_combo.activated.connect(self._on_audio_chosen)
        form.addRow("Output device:", self.device_combo)

        self.buffer_combo = QComboBox()
        for frames in BUFFER_SIZES:
            self.buffer_combo.addItem(
                f"{frames} frames  ({buffer_msec(frames)} ms)", frames
            )
        self.buffer_combo.setCurrentIndex(
            max(0, self.buffer_combo.findData(settings.get_audio_buffer()))
        )
        self.buffer_combo.activated.connect(self._on_audio_chosen)
        form.addRow("Buffer size:", self.buffer_combo)

        hint = QLabel(
            "Smaller buffers respond faster but can crackle; if a preview crackles, choose a larger one."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)

        self.beat_jump_combo = QComboBox()
        for beats in settings.BEAT_JUMP_SIZES:
            self.beat_jump_combo.addItem(
                f"{beats} beat{'s' if beats != 1 else ''}", beats
            )
        self.beat_jump_combo.setCurrentIndex(
            max(0, self.beat_jump_combo.findData(settings.get_beat_jump()))
        )
        self.beat_jump_combo.activated.connect(
            lambda _i: settings.set_beat_jump(self.beat_jump_combo.currentData())
        )
        form.addRow("Beat jump:", self.beat_jump_combo)
        jump_hint = QLabel(
            "How far the left and right arrow keys jump when a track is selected in the list."
        )
        jump_hint.setWordWrap(True)
        jump_hint.setObjectName("mutedLabel")
        form.addRow(jump_hint)

        self.test_button = QPushButton("Play Test Tone")
        self.test_button.clicked.connect(self.play_test_tone)
        row = QHBoxLayout()
        row.addWidget(self.test_button)
        row.addStretch(1)
        form.addRow(row)
        return page

    def _on_audio_chosen(self, _index: int = 0) -> None:
        settings.set_audio_device(self.device_combo.currentData())
        settings.set_audio_buffer(self.buffer_combo.currentData())
        self.audio_changed.emit()

    def play_test_tone(self) -> None:
        """A short tone through the chosen device and buffer, whether or not they are saved yet."""
        self._stop_tone()
        player = AudioPlayer(device_factory=self._device_factory, parent=self)
        player.configure(
            self.device_combo.currentData(), self.buffer_combo.currentData()
        )
        player.failed.connect(lambda text: self.test_button.setToolTip(text))
        player.load(make_test_tone(), autoplay=True)
        self._tone_player = player
        QTimer.singleShot(int(TONE_SECONDS * 1000) + 200, self._stop_tone)

    def _stop_tone(self) -> None:
        if self._tone_player is not None:
            self._tone_player.shutdown()
            self._tone_player.deleteLater()
            self._tone_player = None

    def done(self, result: int) -> None:
        self._stop_tone()
        super().done(result)

    # -- discs ------------------------------------------------------------

    def _discs_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        note = QLabel(
            "How new discs are planned. Existing discs keep the size and gaps they were"
            " made with. When you burn, the disc in the drive is what counts."
        )
        note.setWordWrap(True)
        form.addRow(note)

        self.minutes_combo = QComboBox()
        for minutes in settings.DISC_MINUTES:
            self.minutes_combo.addItem(f"{minutes} minutes", minutes)
        self.minutes_combo.setCurrentIndex(
            max(0, self.minutes_combo.findData(settings.get_disc_minutes()))
        )
        self.minutes_combo.activated.connect(
            lambda _i: settings.set_disc_minutes(self.minutes_combo.currentData())
        )
        form.addRow("Plan new discs for:", self.minutes_combo)

        self.gap_combo = QComboBox()
        for seconds in settings.PREGAP_SECONDS:
            self.gap_combo.addItem(
                "Gapless" if seconds == 0 else f"{seconds} s", seconds
            )
        self.gap_combo.setCurrentIndex(
            max(0, self.gap_combo.findData(settings.get_pregap_seconds()))
        )
        self.gap_combo.activated.connect(
            lambda _i: settings.set_pregap_seconds(self.gap_combo.currentData())
        )
        form.addRow("Between tracks:", self.gap_combo)

        hint = QLabel(
            "Gapless puts no silence between tracks, so the most music fits on a disc."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        return page

    # -- burning ----------------------------------------------------------

    def _burning_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        note = QLabel(
            "What a burn starts with. You can change the speed for a single burn in the burn window."
        )
        note.setWordWrap(True)
        form.addRow(note)

        self.speed_combo = QComboBox()
        for speed in settings.BURN_SPEEDS:
            self.speed_combo.addItem(f"{speed}x", speed)
        self.speed_combo.setCurrentIndex(
            max(0, self.speed_combo.findData(settings.get_burn_speed()))
        )
        self.speed_combo.activated.connect(
            lambda _i: settings.set_burn_speed(self.speed_combo.currentData())
        )
        form.addRow("Burn speed:", self.speed_combo)

        hint = QLabel(
            "Old players read discs burned slowly more reliably (10x to 16x is a good range)."
            " Your drive may not offer every speed."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        return page

    # -- library ----------------------------------------------------------

    def _library_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.auto_import_check = QCheckBox(
            "Update my rekordbox library when Dubcrate starts"
        )
        self.auto_import_check.setChecked(settings.get_auto_import())
        self.auto_import_check.toggled.connect(settings.set_auto_import)
        form.addRow(self.auto_import_check)
        hint = QLabel(
            "Reads a temporary copy of your rekordbox library, so rekordbox is never changed,"
            " and brings in new tracks and changes. It only updates a library you have already"
            " imported. Folders are not re-scanned (use File > Re-import Selected Source)."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        return page

    # -- appearance -------------------------------------------------------

    def _appearance_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.theme_combo = QComboBox()
        for label, value in theme.CHOICES:
            self.theme_combo.addItem(label, value)
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(theme.current_preference()))
        )
        self.theme_combo.activated.connect(
            lambda _i: self.theme_chosen.emit(self.theme_combo.currentData())
        )
        form.addRow("Theme:", self.theme_combo)

        self.wave_style_combo = QComboBox()
        for label, value in wave_style.WAVE_STYLE_LABELS:
            self.wave_style_combo.addItem(label, value)
        self.wave_style_combo.setCurrentIndex(
            max(0, self.wave_style_combo.findData(settings.get_wave_style()))
        )
        self.wave_style_combo.activated.connect(
            lambda _i: wave_style.set_style(self.wave_style_combo.currentData())
        )
        form.addRow("Waveform style:", self.wave_style_combo)

        self.wave_background_combo = QComboBox()
        for label, value in wave_style.WAVE_BACKGROUND_LABELS:
            self.wave_background_combo.addItem(label, value)
        self.wave_background_combo.setCurrentIndex(
            max(0, self.wave_background_combo.findData(settings.get_wave_background()))
        )
        self.wave_background_combo.activated.connect(
            lambda _i: wave_style.set_background(
                self.wave_background_combo.currentData()
            )
        )
        form.addRow("Waveform background:", self.wave_background_combo)

        hint = QLabel(
            "3-Band is rekordbox's default look (lows blue, mids amber, highs white). RGB colours"
            " each moment by what is in it, and Blue is rekordbox's classic blue waveform."
            " The Macintosh System 6 theme always draws the 3-band look in black and white."
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        return page
