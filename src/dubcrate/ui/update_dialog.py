"""The "a new version exists" notice. Adapted from JV Studio's.

It doesn't download or install anything: it points at the GitHub release page. The builds are
not signed or notarised, so a downloaded app needs your own Gatekeeper click-through regardless.
"""

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from dubcrate import settings
from dubcrate.updates import UpdateInfo

from . import theme
from .help_dialog import style_markdown
from .version import app_version


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class UpdateAvailableDialog(QDialog):
    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("Update Available")
        layout = QVBoxLayout(self)

        self.heading = QLabel(f"Dubcrate {info.version} is available")
        self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        layout.addWidget(self.heading)
        current = QLabel(f"You have version {app_version()} installed.")
        current.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(current)

        # the link as text too, so a click that does nothing still leaves a URL to copy
        accent = theme.current_palette()["accent"]
        self.link_label = QLabel(
            f'<a href="{info.html_url}" style="color: {accent};">{info.html_url}</a>'
        )
        self.link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.link_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.link_label.linkActivated.connect(open_url)
        layout.addWidget(self.link_label)

        # The release's own notes, from the Markdown they were written in on GitHub. Left out for
        # a release with no description. Links open in the browser, and only web links: release
        # notes are text from the network, not something that may launch a file:// or custom URL.
        self.notes_view: QTextBrowser | None = None
        if info.notes.strip():
            self.notes_view = QTextBrowser()
            self.notes_view.setOpenLinks(False)
            self.notes_view.setOpenExternalLinks(False)
            self.notes_view.anchorClicked.connect(self._open_notes_link)
            self.notes_view.setMarkdown(info.notes)
            style_markdown(self.notes_view.document())
            self.notes_view.setMinimumHeight(180)
            layout.addWidget(self.notes_view, stretch=1)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Skip This Version")
        self.skip_button.clicked.connect(self._skip_version)
        buttons.addWidget(self.skip_button)
        self.later_button = QPushButton("Remind Me Later")
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.view_button = QPushButton("View Release…")
        self.view_button.setDefault(True)
        self.view_button.clicked.connect(self._open_release_page)
        buttons.addWidget(self.view_button)
        layout.addLayout(buttons)
        self.setMinimumWidth(520)
        if self.notes_view is not None:
            self.resize(560, 460)

    def _open_notes_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            open_url(url.toString())

    def _open_release_page(self) -> None:
        open_url(self.info.html_url)
        self.accept()

    def _skip_version(self) -> None:
        settings.set_skipped_update(self.info.version)
        self.reject()
