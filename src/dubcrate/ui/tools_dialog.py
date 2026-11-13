"""The notice shown at start-up when ffmpeg or ffprobe can't be found.

It says what the tools are for, what still works without them, and how to get them. It never
downloads anything: the release bundles them, so this is only seen from source or a damaged app.
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from dubcrate import tools

from . import theme


class MissingToolsDialog(QDialog):
    def __init__(self, missing: list[str], parent=None):
        super().__init__(parent)
        self.missing = missing
        self.setWindowTitle("ffmpeg Not Found")
        layout = QVBoxLayout(self)

        names = " and ".join(missing)
        self.heading = QLabel(f"Dubcrate can't find {names}")
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        layout.addWidget(self.heading)

        self.body = QLabel(
            "ffmpeg and ffprobe are free programs Dubcrate uses to read the tags and lengths of"
            " music in folders, to play formats it can't read itself, and to prepare discs for"
            " burning.<br><br>"
            "Your library, rekordbox import, the disc planner and previews of MP3, WAV and FLAC"
            " files still work. Folder import and burning won't until they are installed."
        )
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.body)

        layout.addWidget(QLabel("Type this in a terminal, then restart Dubcrate:"))
        row = QHBoxLayout()
        self.command_field = QLineEdit(tools.install_command())
        self.command_field.setReadOnly(True)
        row.addWidget(self.command_field, stretch=1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)
        layout.addLayout(row)

        accent = theme.current_palette()["accent"]
        self.link_label = QLabel(
            f'Or download them from <a href="{tools.DOWNLOAD_URL}" style="color: {accent};">'
            "ffmpeg.org</a>."
        )
        self.link_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.link_label.linkActivated.connect(
            lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        layout.addWidget(self.link_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.ok_button = QPushButton("Carry On")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        self.setMinimumWidth(480)

    def _copy(self) -> None:
        QApplication.clipboard().setText(tools.install_command())
