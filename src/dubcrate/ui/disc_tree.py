"""The tree of discs (right-hand pane), with rekordbox's little + on the picked row.

Every row has one, and it shows only on the row that is picked. Clicking it makes a new one
beside that row: a disc beside a disc (in its folder), a disc inside a disc folder, a folder on
an MP3 disc beside a folder on it. A row an MP3 disc's folders can go in (the disc, or a folder
on it) has a second little button, left of the +, that makes a folder inside that row.
"""

from PySide6.QtCore import QEvent, QModelIndex, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QTreeView

from . import theme
from .models import FOLDER_HOST_ROLE, SELECTION_ROLE

PLUS_SIZE = 14
PLUS_MARGIN = 6
SLOT_WIDTH = (
    PLUS_SIZE + 2 * PLUS_MARGIN
)  # the + has a column of its own, past the count
FOLDER_SLOT = (
    PLUS_SIZE + PLUS_MARGIN
)  # ...and the folder button, left of it, a little more
COLUMN_WIDTH = SLOT_WIDTH + FOLDER_SLOT


class DiscTree(QTreeView):
    # the picked row's selection, ("disc", id) or ("disc_folder", id), whose + was clicked
    new_disc_requested = Signal(tuple)
    # the picked row's selection, whose folder button was clicked: make a folder inside it
    new_folder_requested = Signal(tuple)

    @staticmethod
    def _selection(index: QModelIndex) -> tuple | None:
        return (
            index.siblingAtColumn(0).data(SELECTION_ROLE) if index.isValid() else None
        )

    def plus_visible(self, index: QModelIndex) -> bool:
        """Whether a + is showing on this row: a disc or folder that is picked."""
        if self._selection(index) is None or self.selectionModel() is None:
            return False
        return self.selectionModel().isSelected(index.siblingAtColumn(0))

    def plus_rect(self, index: QModelIndex) -> QRect:
        """The + button's place on a row: in the last column, which is only for it."""
        last = index.siblingAtColumn(self.model().columnCount() - 1)
        cell = self.visualRect(last)
        return QRect(
            cell.right() - PLUS_MARGIN - PLUS_SIZE + 1,
            cell.center().y() - PLUS_SIZE // 2,
            PLUS_SIZE,
            PLUS_SIZE,
        )

    def folder_visible(self, index: QModelIndex) -> bool:
        """Whether the folder button is showing: the row is picked and folders can go in it."""
        return self.plus_visible(index) and bool(
            index.siblingAtColumn(0).data(FOLDER_HOST_ROLE)
        )

    def folder_rect(self, index: QModelIndex) -> QRect:
        """The folder button's place: just left of the +."""
        plus = self.plus_rect(index)
        return QRect(
            plus.left() - PLUS_MARGIN - PLUS_SIZE, plus.top(), PLUS_SIZE, PLUS_SIZE
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            index = self.indexAt(position)
            if self.plus_visible(index) and self.plus_rect(index).contains(position):
                self.new_disc_requested.emit(self._selection(index))
                return  # (not a click on the row: nothing is picked or opened)
            if self.folder_visible(index) and self.folder_rect(index).contains(
                position
            ):
                self.new_folder_requested.emit(self._selection(index))
                return
        super().mousePressEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            position = event.pos()
            index = self.indexAt(position)
            text = ""
            if self.plus_visible(index) and self.plus_rect(index).contains(position):
                kind = (self._selection(index) or ("",))[0]
                text = (
                    "New folder beside this one"
                    if kind == "disc_dir"
                    else "New disc beside this one"
                    if kind == "disc"
                    else "New disc in this folder"
                )
            elif self.folder_visible(index) and self.folder_rect(index).contains(
                position
            ):
                text = "New folder on the disc, inside this"
            if text:
                QToolTip.showText(event.globalPos(), text, self)
                return True
        return super().viewportEvent(event)

    def drawRow(self, painter: QPainter, option, index: QModelIndex) -> None:
        super().drawRow(painter, option, index)
        if not self.plus_visible(index):
            return
        palette = theme.current_palette()
        rect = self.plus_rect(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["text_disabled"]))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(palette["bg_panel"]), 1.6))
        centre = QPointF(rect.center()) + QPointF(0.5, 0.5)
        arm = PLUS_SIZE / 2 - 4
        painter.drawLine(centre + QPointF(-arm, 0), centre + QPointF(arm, 0))
        painter.drawLine(centre + QPointF(0, -arm), centre + QPointF(0, arm))
        painter.restore()
        if self.folder_visible(index):
            self._draw_folder_button(painter, self.folder_rect(index), palette)

    @staticmethod
    def _draw_folder_button(painter: QPainter, rect: QRect, palette) -> None:
        """A disc like the +, with a little folder in it."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["text_disabled"]))
        painter.drawEllipse(rect)
        painter.setBrush(QColor(palette["bg_panel"]))
        cx, cy = rect.center().x() + 0.5, rect.center().y() + 0.5
        painter.drawRect(QRectF(cx - 4, cy - 3, 3.5, 1.6))  # the folder's tab...
        painter.drawRect(QRectF(cx - 4, cy - 1.6, 8, 5))  # ...and its body
        painter.restore()
