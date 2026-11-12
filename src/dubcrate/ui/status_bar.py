"""The window's status bar: "Dubcrate v0.1.0" at the left, an "Update available" button beside it
when there is one, then the messages (what was added, imported, burned) after them.

A plain QStatusBar hides its left-hand widgets while a message shows, so the version would
vanish every time a message appeared. This one keeps its own label for messages instead, and
answers `showMessage`, `clearMessage` and `currentMessage` as a QStatusBar does, so the rest of
the window talks to it as before. The little resize grip in the corner is switched off: it
doesn't look right on the window's background.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStatusBar, QWidget

from dubcrate.updates import UpdateInfo

from .version import version_text


class AppStatusBar(QStatusBar):
    update_clicked = Signal()  # the "Update available" button was pressed

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self.version_label = QLabel(version_text())
        self.version_label.setObjectName("versionLabel")
        self.update_button = QPushButton("Update available")
        self.update_button.setObjectName("updateButton")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.update_button.clicked.connect(self.update_clicked)
        self.update_button.hide()
        self.message_label = QLabel()
        self.message_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(self.version_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.update_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addSpacing(6)
        layout.addWidget(self.message_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.addWidget(row, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.clearMessage)

    # -- messages, as a QStatusBar has them -------------------------------------------

    def showMessage(self, text: str, timeout: int = 0) -> None:
        """Show `text` after the version; it goes away after `timeout` ms (0: stays)."""
        self.message_label.setText(text)
        self.messageChanged.emit(text)
        self._timer.stop()
        if timeout > 0:
            self._timer.start(timeout)

    def clearMessage(self) -> None:
        self._timer.stop()
        if self.message_label.text():
            self.message_label.clear()
            self.messageChanged.emit("")

    def currentMessage(self) -> str:
        return self.message_label.text()

    # -- the update button ----------------------------------------------------------------

    def set_update(self, info: UpdateInfo | None) -> None:
        """Show the button for release `info`, or hide it (None: up to date, or not checked)."""
        self.update_button.setVisible(info is not None)
        if info is not None:
            self.update_button.setToolTip(f"Dubcrate {info.version} is available. Click for what's new.")  # fmt: skip
