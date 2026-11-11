"""The tree of discs (right-hand pane), with rekordbox's little + on the picked row.

Every row has one, and it shows only on the row that is picked. Clicking it makes a disc
beside that row: inside it if it is a folder, in the same folder if it is a disc.
"""

from PySide6.QtCore import QModelIndex, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QTreeView

from . import theme
from .models import SELECTION_ROLE

PLUS_SIZE = 14
PLUS_MARGIN = 6


class DiscTree(QTreeView):
    # the picked row's selection, ("disc", id) or ("disc_folder", id), whose + was clicked
    new_disc_requested = Signal(tuple)

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
        """The + button's place on a row: the right-hand end of its last column."""
        last = index.siblingAtColumn(self.model().columnCount() - 1)
        cell = self.visualRect(last)
        return QRect(
            cell.right() - PLUS_MARGIN - PLUS_SIZE + 1,
            cell.center().y() - PLUS_SIZE // 2,
            PLUS_SIZE,
            PLUS_SIZE,
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            index = self.indexAt(position)
            if self.plus_visible(index) and self.plus_rect(index).contains(position):
                self.new_disc_requested.emit(self._selection(index))
                return  # (not a click on the row: nothing is picked or opened)
        super().mousePressEvent(event)

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
