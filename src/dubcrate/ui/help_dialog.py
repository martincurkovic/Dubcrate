"""Help > User Guide: renders help/manual.md, the one copy of the guide.

Ported from AKAISDS's Quick Start dialog (find in page, `[links](#headings)` that
jump within the page, screenshots that shrink to fit the window), with the callout
boxes from akaiutil-GUI's manual. It is not modal, so it can stay open beside the
window it explains. The help folder ships inside the package.
"""

import re
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPalette,
    QPixmap,
    QShortcut,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextFrameFormat,
    QTextLength,
    QTextTableFormat,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme

HELP_DIR = Path(__file__).parent / "help"
MANUAL_PATH = HELP_DIR / "manual.md"

# Screenshots are captured at twice their on-screen size (so they stay sharp on a
# Retina display). QTextDocument doesn't fit images to its viewport the way a
# browser does, so each image's width is set here: shrunk to fit a narrow window,
# never past what the screenshot measured on screen.
SCREENSHOT_PIXEL_RATIO = 2.0
_IMAGE_WIDTH_MARGIN = 24
_RESIZE_DEBOUNCE_MS = 60  # re-measure once a live resize has paused this long

# Fixed highlight colours, as in a browser's find-in-page. A plain text selection
# is painted dim whenever the viewer lacks focus, and focus sits in the search box
# while searching; ExtraSelections paint regardless.
_MATCH_BACKGROUND = QColor("#f5c518")
_MATCH_FOREGROUND = QColor("#1a1a1a")
_CURRENT_MATCH_BACKGROUND = QColor("#ff8f00")
_CURRENT_MATCH_FOREGROUND = QColor("#1a1a1a")


def heading_slug(text: str) -> str:
    """GitHub's anchor for a heading (lower case, punctuation dropped, spaces to
    hyphens, runs of hyphens kept), so the same `#some-heading` links work on
    GitHub and here."""
    return re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")


def _fragments(document):
    """Every text fragment in the document, in order."""
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                yield fragment
            it += 1
        block = block.next()


def _recolor_links(document, color: QColor, underline: bool) -> None:
    """Qt draws links in its own blue whatever the palette says (unreadable on the
    dark theme), so give each link the theme's colour. A theme whose link colour is
    the text colour (System 6) also underlines them, to tell them from the text."""
    cursor = QTextCursor(document)
    fmt = QTextCharFormat()
    fmt.setForeground(color)
    if underline:
        fmt.setFontUnderline(True)
    for fragment in list(_fragments(document)):
        if fragment.charFormat().isAnchor():
            cursor.setPosition(fragment.position())
            cursor.setPosition(
                fragment.position() + fragment.length(),
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.mergeCharFormat(fmt)


def _restyle_code_runs(document) -> None:
    """In a theme with a font of its own (System 6), give inline code that font and
    the size of the text around it: Markdown marks a code span "monospace" at the
    document's own point size, which is the system's fixed font and bigger."""
    palette = theme.current_palette()
    size = palette.get("font_size_dense")
    family = palette.get("font_family")
    if not family or not size:
        return
    fmt = QTextCharFormat()
    fmt.setFontFamilies([f.strip().strip("\"'") for f in family.split(",")])
    fmt.setFontFixedPitch(False)
    fmt.setProperty(QTextFormat.Property.FontPixelSize, int(size.removesuffix("px")))
    cursor = QTextCursor(document)
    for fragment in list(_fragments(document)):
        if fragment.charFormat().fontFixedPitch():
            cursor.setPosition(fragment.position())
            cursor.setPosition(
                fragment.position() + fragment.length(),
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.mergeCharFormat(fmt)


def style_markdown(document) -> None:
    """What a theme needs done to a Markdown document once it is loaded: links in its colour
    (Qt draws them dark blue), and in a theme with a font of its own, inline code in that font.
    The guide, the third-party notices and the release notes all go through this."""
    palette = theme.current_palette()
    link = palette.get("link")  # only a theme that sets one underlines its links
    _recolor_links(document, QColor(link or palette["accent"]), underline=bool(link))
    _restyle_code_runs(document)


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Guide")
        self.setMinimumSize(480, 360)
        self.resize(820, 700)
        self._matches: list[tuple[int, int]] = []
        self._current = -1
        self._natural_sizes: dict[str, tuple[float, float]] = {}  # read from disk once

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        self.viewer.setSearchPaths([str(HELP_DIR)])  # the guide's relative image paths
        self.loaded = False
        self.search_bar = (
            self._build_search_bar()
        )  # before _load, which refreshes a search
        self.search_bar.setVisible(False)
        self._load()

        layout = QVBoxLayout(self)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.viewer)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(
            lambda: self._fit_images(self._target_width())
        )
        self.viewer.viewport().installEventFilter(self)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self).activated.connect(
            self.open_search
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close_search)
        theme.notifier.changed.connect(self._load)  # colours are baked in on load

    # -- loading ------------------------------------------------------------

    def _load(self) -> None:
        """(Re)read the guide and style it for the current theme."""
        self.loaded = False
        try:
            text = MANUAL_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            self.viewer.setPlainText(f"Couldn't load the user guide: {exc}")
            return
        self.viewer.setMarkdown(text)
        document = self.viewer.document()
        style_markdown(document)
        self._style_blockquotes()
        self._fit_images(self._target_width())
        self._add_heading_anchors()
        self.loaded = True
        self._matches, self._current = [], -1
        if self.search_bar_visible():
            self._on_text_changed(self.search_input.text())

    # -- images -------------------------------------------------------------

    def _target_width(self) -> int:
        return max(1, self.viewer.viewport().width() - _IMAGE_WIDTH_MARGIN)

    def _natural_size(self, name: str) -> tuple[float, float] | None:
        cached = self._natural_sizes.get(name)
        if cached is not None:
            return cached
        pixmap = QPixmap(str(HELP_DIR / name))
        if pixmap.isNull():
            return None
        size = (
            pixmap.width() / SCREENSHOT_PIXEL_RATIO,
            pixmap.height() / SCREENSHOT_PIXEL_RATIO,
        )
        self._natural_sizes[name] = size
        return size

    def _fit_images(self, max_width: float) -> None:
        """Set every image's width to fit `max_width` (never above its own size).
        Safe to repeat: an image that was shrunk grows back when the window does."""
        document = self.viewer.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()  # one edit for all of them: a relayout per image lags
        try:
            for fragment in list(_fragments(document)):
                fmt = fragment.charFormat()
                if not fmt.isImageFormat():
                    continue
                image = fmt.toImageFormat()
                natural = self._natural_size(image.name())
                if natural is None:
                    continue
                width = min(max_width, natural[0])
                image.setWidth(width)
                image.setHeight(natural[1] * width / natural[0])
                cursor.setPosition(fragment.position())
                cursor.setPosition(
                    fragment.position() + fragment.length(),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.setCharFormat(image)
        finally:
            cursor.endEditBlock()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self.viewer.viewport()
            and event.type() == QEvent.Type.Resize
            and self.loaded
        ):
            self._resize_timer.start(_RESIZE_DEBOUNCE_MS)
        return super().eventFilter(watched, event)

    # -- callouts and anchors ----------------------------------------------

    def _style_blockquotes(self) -> None:
        """Qt renders a `> quote` as nothing but an indented paragraph; put each one
        in a tinted, bordered box so it reads as a callout. The tint is the
        palette's highlight colour at low alpha, so it suits every theme."""
        document = self.viewer.document()
        accent = self.viewer.palette().color(QPalette.ColorRole.Highlight)
        tint, edge = QColor(accent), QColor(accent)
        tint.setAlpha(36)
        edge.setAlpha(140)
        groups: list[tuple[int, int]] = []  # (start, end) of each run of quoted blocks
        block = document.begin()
        while block.isValid():
            quoted = block.blockFormat().intProperty(
                QTextFormat.Property.BlockQuoteLevel
            )
            if quoted > 0:
                end = block.position() + block.length() - 1
                if groups and groups[-1][1] + 1 == block.position():
                    groups[-1] = (groups[-1][0], end)
                else:
                    groups.append((block.position(), end))
            block = block.next()
        for start, end in reversed(groups):  # last first, so earlier positions hold
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fragment = cursor.selection()
            cursor.removeSelectedText()
            table_format = QTextTableFormat()
            table_format.setBorder(1)
            table_format.setBorderBrush(edge)
            table_format.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
            table_format.setCellPadding(8)
            table_format.setCellSpacing(0)
            table_format.setBackground(tint)
            table_format.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
            table = cursor.insertTable(1, 1, table_format)
            cell = table.cellAt(0, 0)
            cell.firstCursorPosition().insertFragment(fragment)
            inner = QTextCursor(
                document
            )  # the quote's own indent would sit inside the box
            inner.setPosition(cell.firstPosition())
            inner.setPosition(cell.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
            inner.setBlockFormat(QTextBlockFormat())

    def _add_heading_anchors(self) -> None:
        """Qt turns `[link](#heading)` into a link but never names the heading it
        points at; give each heading GitHub's anchor."""
        document = self.viewer.document()
        block = document.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0 and block.text():
                cursor = QTextCursor(document)
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + len(block.text()),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                fmt = QTextCharFormat()
                fmt.setAnchor(True)
                fmt.setAnchorNames([heading_slug(block.text())])
                cursor.mergeCharFormat(fmt)
            block = block.next()

    # -- find in page --------------------------------------------------------

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("transparentContainer")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 6)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in page…")
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.returnPressed.connect(self.find_next)
        self.results_label = QLabel("")
        self.results_label.setFixedWidth(70)
        previous, following, close = (
            QPushButton("←"),
            QPushButton("→"),
            QPushButton("×"),
        )
        for button in (previous, following, close):
            button.setAutoDefault(False)  # Return belongs to the search box
        previous.setToolTip("Previous match")
        following.setToolTip("Next match")
        close.setToolTip("Close the search bar (Esc)")
        previous.clicked.connect(self.find_previous)
        following.clicked.connect(self.find_next)
        close.clicked.connect(self.close_search)
        for w in (self.search_input, self.results_label, previous, following, close):
            row.addWidget(w)
        return bar

    def search_bar_visible(self) -> bool:
        return not self.search_bar.isHidden()

    def open_search(self) -> None:
        self.search_bar.setVisible(True)
        self.search_input.setFocus()
        self.search_input.selectAll()
        if self.search_input.text():
            self._on_text_changed(self.search_input.text())

    def close_search(self) -> None:
        if not self.search_bar_visible():
            return
        self.search_bar.setVisible(False)
        self.viewer.setExtraSelections([])
        self.viewer.setFocus()

    def _on_text_changed(self, text: str) -> None:
        self._matches = self._find_all(text) if text else []
        self._current = 0 if self._matches else -1
        if self._matches:
            self._go_to(self._current)
        else:
            self.viewer.setExtraSelections([])
            self._update_label()

    def _find_all(self, text: str) -> list[tuple[int, int]]:
        found = []
        document = self.viewer.document()
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(text, cursor)
            if cursor.isNull():
                return found
            found.append((cursor.selectionStart(), cursor.selectionEnd()))

    def _go_to(self, index: int) -> None:
        self._current = index
        cursor = self.viewer.textCursor()
        cursor.setPosition(self._matches[index][0])
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()
        self._highlight()
        self._update_label()

    def _highlight(self) -> None:
        selections = []
        for i, (start, end) in enumerate(self._matches):
            cursor = QTextCursor(self.viewer.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            current = i == self._current
            fmt.setBackground(
                _CURRENT_MATCH_BACKGROUND if current else _MATCH_BACKGROUND
            )
            fmt.setForeground(
                _CURRENT_MATCH_FOREGROUND if current else _MATCH_FOREGROUND
            )
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)

    def find_next(self) -> None:
        if self._matches:
            self._go_to((self._current + 1) % len(self._matches))

    def find_previous(self) -> None:
        if self._matches:
            self._go_to((self._current - 1) % len(self._matches))

    def _update_label(self) -> None:
        if not self.search_input.text():
            self.results_label.setText("")
        elif not self._matches:
            self.results_label.setText("0 of 0")
        else:
            self.results_label.setText(f"{self._current + 1} of {len(self._matches)}")


def show_help_dialog(parent) -> HelpDialog:
    """One guide window, reused: choosing Help > User Guide again raises the open
    one rather than stacking another."""
    dialog = getattr(parent, "_help_dialog", None)
    if dialog is None:
        dialog = HelpDialog(parent)
        parent._help_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
