"""Help > About Dubcrate, and the third-party notices it links to."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .help_dialog import HELP_DIR, style_markdown
from .update_check import UpdateChecker
from .version import app_version

NOTICES_PATH = HELP_DIR / "THIRD_PARTY_NOTICES.md"


class NoticesDialog(QDialog):
    """The licence notices for what Dubcrate includes (help/THIRD_PARTY_NOTICES.md)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Third-Party Notices")
        self.resize(680, 520)
        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        self._load()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.viewer)
        layout.addWidget(buttons)
        theme.notifier.changed.connect(self._load)  # colours are baked in on load

    def _load(self) -> None:
        try:
            text = NOTICES_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            self.viewer.setPlainText(f"Couldn't load the notices: {exc}")
            return
        self.viewer.setMarkdown(text)
        style_markdown(self.viewer.document())


ABOUT_WIDTH = (
    460  # the text column's width: the window is as wide as this and the margins
)
LOGO_SIZE = 80  # the icon's size in points (it is drawn at the screen's real pixels)


def logo_pixmap(path: Path, size: int, ratio: float) -> QPixmap:
    """The icon `size` points square, made of `size * ratio` real pixels and marked with that
    ratio, so a Retina screen shows it sharp instead of stretching a small picture. Null if the
    file can't be read."""
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    side = round(size * ratio)
    pixmap = pixmap.scaled(
        side,
        side,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


class AboutDialog(QDialog):
    def __init__(
        self, parent: QWidget | None, checker: UpdateChecker, icon: Path | None
    ):
        super().__init__(parent)
        self.setWindowTitle("About Dubcrate")
        self._checker = checker
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        # a fixed size: the text wraps at a set width, so the window is exactly as tall as it needs,
        # and cannot be dragged smaller than its text
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        # the icon at the top left, the name beside it and the version just under the name
        header = QHBoxLayout()
        header.setSpacing(16)
        self.logo: QLabel | None = None
        if icon is not None:
            pixmap = logo_pixmap(icon, LOGO_SIZE, self.devicePixelRatioF())
            if not pixmap.isNull():
                self.logo = QLabel()
                self.logo.setPixmap(pixmap)
                self.logo.setFixedSize(LOGO_SIZE, LOGO_SIZE)
                header.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        # (the size is in the text: the theme's stylesheet decides a label's font and would
        # override a QFont set here)
        self.name_label = QLabel(
            '<span style="font-size:22pt; font-weight:bold">Dubcrate</span>'
        )
        self.version_label = QLabel(f"Version {app_version()}")
        self.version_label.setObjectName("mutedLabel")
        titles.addStretch()
        titles.addWidget(self.name_label)
        titles.addWidget(self.version_label)
        titles.addStretch()
        header.addLayout(titles, 1)
        layout.addLayout(header)

        body = QLabel(
            "Burn audio CDs for old-style CDJs from your DJ library.<br>"
            "Free software, licensed under the GNU General Public License, version 3 or later.<br><br>"
            "rekordbox, Pioneer DJ, AlphaTheta and CDJ are trademarks of their owners; Apple and "
            "Macintosh are trademarks of Apple Inc. This project is not affiliated with any of them."
        )
        credits = QLabel(
            "The Macintosh System 6 theme is an homage; its font, ChiKareGo2, is by Giles Booth "
            "(CC BY). The licences of everything else Dubcrate includes are under "
            "Third-Party Notices."
        )
        credits.setObjectName("mutedLabel")
        for text in (body, credits):
            text.setWordWrap(True)
            text.setFixedWidth(ABOUT_WIDTH)
            layout.addWidget(text)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.update_button = QPushButton("Check for Updates")
        self.update_button.clicked.connect(self._check_for_updates)
        buttons.addWidget(self.update_button)
        self.notices_button = QPushButton("Third-Party Notices…")
        self.notices_button.clicked.connect(self.show_notices)
        buttons.addWidget(self.notices_button)
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.notices: NoticesDialog | None = None

    def show_notices(self) -> None:
        self.notices = NoticesDialog(self)
        self.notices.show()

    def _check_for_updates(self) -> None:
        self.update_button.setEnabled(False)
        # (the answer appears over this window, which is modal: its own children stay usable)
        self._checker.start(manual=True, on_finished=self._finished, parent=self)

    def _finished(self) -> None:
        self.update_button.setEnabled(True)
