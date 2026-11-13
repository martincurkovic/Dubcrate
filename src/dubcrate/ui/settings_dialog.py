"""Settings: where audio goes, how big the buffer is, and how the app looks."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from dubcrate import keys, loudness, mp3, settings, tools
from dubcrate.audio import devices
from dubcrate.audio.devices import BUFFER_SIZES, buffer_msec

from . import key_style, theme, wave_style
from .full_width_tabs import FullWidthTabBar


class SettingsDialog(QDialog):
    audio_changed = Signal()
    theme_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)

        tabs = QTabWidget()
        tabs.setTabBar(FullWidthTabBar(tabs))  # before any tab is added
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

        return page

    def _on_audio_chosen(self, _index: int = 0) -> None:
        settings.set_audio_device(self.device_combo.currentData())
        settings.set_audio_buffer(self.buffer_combo.currentData())
        self.audio_changed.emit()

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
            self.minutes_combo.addItem(
                f"{minutes} minutes ({settings.DISC_BOX_MB[minutes]} MB)", minutes
            )
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

        self.mp3_combo = QComboBox()
        for kbps in mp3.BITRATES:
            self.mp3_combo.addItem(f"{kbps} kbit/s", kbps)
        self.mp3_combo.setCurrentIndex(
            max(0, self.mp3_combo.findData(settings.get_mp3_bitrate()))
        )
        self.mp3_combo.activated.connect(
            lambda _i: settings.set_mp3_bitrate(self.mp3_combo.currentData())
        )
        form.addRow("MP3 CDs: highest rate:", self.mp3_combo)
        mp3_hint = QLabel(
            "For new MP3 discs. Files are always constant bitrate (variable-bitrate files load"
            " slowly on a CDJ), at this rate, or lower for a lossy track that is below it."
            " MP3 files go on the disc as they are, never re-encoded."
        )
        mp3_hint.setWordWrap(True)
        mp3_hint.setObjectName("mutedLabel")
        form.addRow(mp3_hint)
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

        # normalising the levels: audio CDs only (see loudness.py)
        self.levels_check = QCheckBox("Normalise the levels of tracks on audio CDs")
        self.levels_check.setChecked(settings.get_levels_enabled())
        self.levels_check.toggled.connect(self._on_levels_toggled)
        form.addRow(self.levels_check)
        self.levels_combo = QComboBox()
        for preset in loudness.PRESETS:
            self.levels_combo.addItem(preset.label, preset.key)
        self.levels_combo.setCurrentIndex(
            max(0, self.levels_combo.findData(settings.get_levels_choice()))
        )
        self.levels_combo.setEnabled(self.levels_check.isChecked())
        self.levels_combo.activated.connect(
            lambda _i: settings.set_levels_choice(self.levels_combo.currentData())
        )
        form.addRow("Normalise to:", self.levels_combo)
        levels_hint = QLabel(
            "Each track on an audio CD gets one fixed volume change: no compression or limiting, so the"
            " music keeps its dynamics. The loudness choices bring the tracks to about the same level, but"
            " never turn one up past the point where its peaks would clip (those are lifted less, and the"
            " burn window says so). Peak to full scale makes each track as loud as it can safely be"
            " instead, so levels still differ from track to track. The first burn takes a moment longer"
            " while the tracks are measured. MP3 discs are left as they are. You can change this for a"
            " single burn in the burn window."
        )
        levels_hint.setWordWrap(True)
        levels_hint.setObjectName("mutedLabel")
        form.addRow(levels_hint)
        return page

    def _on_levels_toggled(self, on: bool) -> None:
        settings.set_levels_enabled(on)
        self.levels_combo.setEnabled(on)

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
        self.auto_update_check = QCheckBox(
            "Check for a new version when Dubcrate starts"
        )
        self.auto_update_check.setChecked(settings.get_auto_update_check())
        self.auto_update_check.toggled.connect(settings.set_auto_update_check)
        form.addRow(self.auto_update_check)
        update_hint = QLabel(
            "Asks github.com whether a newer release exists, at most once a day. That is the only"
            " time Dubcrate uses the network, and nothing is downloaded or installed."
            " You can always check from Help > Check for Updates."
        )
        update_hint.setWordWrap(True)
        update_hint.setObjectName("mutedLabel")
        form.addRow(update_hint)
        form.addRow(QLabel("Tools Dubcrate runs:"))
        self.tool_labels: dict[str, QLabel] = {}
        for name in tools.REQUIRED:
            label = QLabel(tools.describe(name))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.tool_labels[name] = label
            form.addRow(label)
        tools_hint = QLabel(
            "ffmpeg reads and converts your music and ffprobe reads its tags and lengths."
            " Dubcrate uses the copy it comes with, else one on your PATH."
            + (
                f" A missing one can be installed with “{tools.install_command()}”."
                if tools.missing_tools()
                else ""
            )
        )
        tools_hint.setWordWrap(True)
        tools_hint.setObjectName("mutedLabel")
        form.addRow(tools_hint)
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

        self.key_combo = QComboBox()
        self.key_combo.addItem("Camelot (8A)", keys.CAMELOT)
        self.key_combo.addItem("Musical (A min)", keys.MUSICAL)
        self.key_combo.setCurrentIndex(
            max(0, self.key_combo.findData(settings.get_key_notation()))
        )
        self.key_combo.setToolTip(
            "How keys are written in the track list and the player. Only what is shown changes:"
            " your library, rekordbox and the files keep what they have."
        )
        self.key_combo.activated.connect(
            lambda _i: key_style.set_notation(self.key_combo.currentData())
        )
        form.addRow("Key notation:", self.key_combo)

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
