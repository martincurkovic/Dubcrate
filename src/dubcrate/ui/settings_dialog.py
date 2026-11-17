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

from dubcrate import keys, logsetup, loudness, mp3, settings, tools, units
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
        # as big as the biggest tab needs, so no heading or row is ever clipped
        need = self.sizeHint()
        self.setMinimumSize(max(need.width(), 580), need.height())

    # -- audio ------------------------------------------------------------

    def _audio_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
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
        self.device_combo.setToolTip("Where previews play. Changes apply at once.")
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
        self.buffer_combo.setToolTip(
            "A smaller buffer responds faster but can crackle: if a preview does, choose a larger one."
        )
        form.addRow("Buffer size:", self.buffer_combo)

        self.beat_jump_combo = QComboBox()
        for beats in settings.BEAT_JUMP_SIZES:
            self.beat_jump_combo.addItem(settings.beat_jump_label(beats), beats)
        self.beat_jump_combo.setCurrentIndex(
            max(0, self.beat_jump_combo.findData(settings.get_beat_jump()))
        )
        self.beat_jump_combo.activated.connect(
            lambda _i: settings.set_beat_jump(self.beat_jump_combo.currentData())
        )
        self.beat_jump_combo.setToolTip("How far the left and right arrow keys jump.")
        form.addRow("Beat jump:", self.beat_jump_combo)

        return page

    def _on_audio_chosen(self, _index: int = 0) -> None:
        settings.set_audio_device(self.device_combo.currentData())
        settings.set_audio_buffer(self.buffer_combo.currentData())
        self.audio_changed.emit()

    # -- discs ------------------------------------------------------------

    def _discs_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
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
        self.minutes_combo.setToolTip(
            "How new discs are planned. Existing discs keep their own size and gaps."
        )
        form.addRow("Default CD-R size:", self.minutes_combo)

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
        self.gap_combo.setToolTip("Gapless fits the most music on a disc.")
        form.addRow("Between tracks:", self.gap_combo)

        self.mp3_combo = QComboBox()
        for kbps in mp3.BITRATES:
            self.mp3_combo.addItem(f"{kbps} kbit/s", kbps)
        self.mp3_combo.setCurrentIndex(
            max(0, self.mp3_combo.findData(settings.get_mp3_bitrate()))
        )
        self.mp3_combo.activated.connect(
            lambda _i: settings.set_mp3_bitrate(self.mp3_combo.currentData())
        )
        self.mp3_combo.setToolTip(
            "For new MP3 discs. Files are always constant bitrate, at this rate or lower for a lossy"
            " track below it. MP3 files are never re-encoded."
        )
        form.addRow("MP3 CDs: highest rate:", self.mp3_combo)
        return page

    # -- burning ----------------------------------------------------------

    def _burning_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.speed_combo = QComboBox()
        for speed in settings.BURN_SPEEDS:
            self.speed_combo.addItem(f"{speed}x", speed)
        self.speed_combo.setCurrentIndex(
            max(0, self.speed_combo.findData(settings.get_burn_speed()))
        )
        self.speed_combo.activated.connect(
            lambda _i: settings.set_burn_speed(self.speed_combo.currentData())
        )
        self.speed_combo.setToolTip(
            "Old players read slow burns more reliably (10x to 16x is a good range)."
            " Your drive may not offer every speed. The burn window can change it for one burn."
        )
        form.addRow("Burn speed:", self.speed_combo)

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
        self.levels_combo.setToolTip(
            "Each track gets one fixed volume change, with no compression or limiting. The first burn is"
            " slower while tracks are measured. The burn window can change this for one burn."
        )
        note = QLabel("MP3 discs are not normalised.")
        note.setObjectName("mutedLabel")
        form.addRow(note)
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
        self.auto_import_check.setToolTip(
            "Reads a temporary copy of your rekordbox library (importing never changes it) for new"
            " tracks and changes. Folders are not re-scanned."
        )
        self.rekordbox_edit_check = QCheckBox(
            "Save memory cue and loop changes into my rekordbox library"
        )
        self.rekordbox_edit_check.setChecked(bool(settings.get_rekordbox_editing()))
        self.rekordbox_edit_check.toggled.connect(settings.set_rekordbox_editing)
        form.addRow(self.rekordbox_edit_check)
        self.rekordbox_edit_check.setToolTip(
            "Memory cue and loop changes you make in the player are written into rekordbox's library, and"
            " nothing else is. rekordbox must be closed, or the change waits in Dubcrate. The database files"
            " are copied aside first, and a write that doesn't check out is undone. Unticked, changes stay in"
            " Dubcrate."
        )
        self.auto_update_check = QCheckBox(
            "Check for a new version when Dubcrate starts"
        )
        self.auto_update_check.setChecked(settings.get_auto_update_check())
        self.auto_update_check.toggled.connect(settings.set_auto_update_check)
        form.addRow(self.auto_update_check)
        self.auto_update_check.setToolTip(
            "Asks github.com at most once a day: the only time Dubcrate uses the network, and nothing is"
            " downloaded. Help > Check for Updates checks any time."
        )
        form.addRow(QLabel("Tools Dubcrate runs:"))
        self.tool_labels: dict[str, QLabel] = {}
        for name in tools.REQUIRED:
            label = QLabel(tools.describe(name))
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.tool_labels[name] = label
            form.addRow(label)
        if tools.missing_tools():  # (only worth a line when something needs doing)
            tools_hint = QLabel(
                f"Install a missing one with “{tools.install_command()}”."
            )
            tools_hint.setWordWrap(True)
            tools_hint.setObjectName("mutedLabel")
            form.addRow(tools_hint)
        self.verbose_check = QCheckBox("Verbose logging")
        self.verbose_check.setChecked(
            settings.get_verbose_logging() or logsetup.is_verbose()
        )
        self.verbose_check.toggled.connect(self._set_verbose)
        form.addRow(self.verbose_check)
        self.verbose_check.setToolTip(
            "The log (Help > Show Log File) then lists what Dubcrate did, step by step: file paths, never"
            " audio. Takes effect at once."
        )
        return page

    @staticmethod
    def _set_verbose(on: bool) -> None:
        settings.set_verbose_logging(on)
        logsetup.set_verbose(on)

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

        self.units_combo = QComboBox()
        for u in units.UNITS:
            self.units_combo.addItem(u.name, u.key)
        self.units_combo.setCurrentIndex(
            max(0, self.units_combo.findData(settings.get_units()))
        )
        self.units_combo.setToolTip(
            "What sizes are shown in, such as the label window's. Sizes are stored in millimetres, so changing this"
            " loses nothing."
        )
        self.units_combo.activated.connect(
            lambda _i: settings.set_units(self.units_combo.currentData())
        )
        form.addRow("Units:", self.units_combo)

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

        self.wave_fps_combo = QComboBox()
        for label, value in wave_style.WAVE_FPS_LABELS:
            self.wave_fps_combo.addItem(label, value)
        self.wave_fps_combo.setCurrentIndex(
            max(0, self.wave_fps_combo.findData(settings.get_wave_fps()))
        )
        self.wave_fps_combo.setToolTip(
            "How many times a second the playing waveform moves. 60 is smooth; choose 30 on a slower"
            " computer to use less of it (the sound is not affected either way)."
        )
        self.wave_fps_combo.activated.connect(
            lambda _i: settings.set_wave_fps(self.wave_fps_combo.currentData())
        )
        form.addRow("Waveform speed:", self.wave_fps_combo)

        hint = QLabel("System 6 theme always uses 3-Band waveforms in greyscale")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        form.addRow(hint)
        return page
