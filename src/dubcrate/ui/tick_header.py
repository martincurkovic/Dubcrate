"""A tree header with a tick box in its first heading that ticks (or unticks) everything below it.

Used by the Sync tab's disc list and the Import Cues… review. The box is drawn as the tree's own boxes
are (the item view's check primitive), so it looks the same in every theme."""

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionViewItem, QWidget

COL_BOX = 0  # the heading that has the box


class TickHeader(QHeaderView):
    """The disc list's header, with a tick box at the left of the first column that ticks (or unticks) every disc
    that can be synced, drawn as the list's own boxes are. It shows ticked, unticked or partly from the discs."""

    tick_clicked = Signal()
    BOX_AT = 7  # the box's distance from where the tree's indentation ends: lines up with the list's own boxes

    def __init__(self, parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._state = Qt.CheckState.Unchecked
        self._available = (
            False  # no disc can be ticked: the box is drawn dim and does nothing
        )
        self._indent = (
            20  # the tree's indentation: the boxes of the top level discs sit after it
        )
        self.setSectionsClickable(True)
        self.set_indent(self._indent)

    def box_width(self) -> int:
        # (the list's own boxes are a little bigger than a plain check box's indicator)
        return 4 + self.style().pixelMetric(
            QStyle.PixelMetric.PM_IndicatorWidth, None, self
        )

    def set_indent(self, indent: int) -> None:
        self._indent = indent
        # room in the first heading for the box (the style sheet pads a heading by 8 pixels)
        self.setStyleSheet(
            f"QHeaderView::section:first {{ padding-left: {indent + self.BOX_AT + self.box_width() + 6}px; }}"
        )
        # (a style sheet on the header would centre the headings: they were left aligned)
        self.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

    def set_state(self, state: Qt.CheckState, available: bool) -> None:
        if (state, available) != (self._state, self._available):
            self._state, self._available = state, available
            self.viewport().update()

    def _box(self, section: QRect) -> QRect:
        size = self.box_width()
        return QRect(
            section.left() + self._indent + self.BOX_AT,
            section.top() + (section.height() - size) // 2,
            size,
            size,
        )

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != COL_BOX:
            return
        option = QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = self._box(rect)
        option.state = QStyle.StateFlag.State_Active | (
            QStyle.StateFlag.State_Enabled
            if self._available
            else QStyle.StateFlag.State_None
        )
        option.state |= {
            Qt.CheckState.Checked: QStyle.StateFlag.State_On,
            Qt.CheckState.PartiallyChecked: QStyle.StateFlag.State_NoChange,
            Qt.CheckState.Unchecked: QStyle.StateFlag.State_Off,
        }[self._state]
        self.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, option, painter, self
        )

    def mousePressEvent(self, event) -> None:
        if (
            self._available
            and event.button() == Qt.MouseButton.LeftButton
            and self.logicalIndexAt(event.position().toPoint()) == COL_BOX
        ):
            section = QRect(
                self.sectionViewportPosition(COL_BOX),
                0,
                self.sectionSize(COL_BOX),
                self.height(),
            )
            if (
                self._box(section)
                .adjusted(-4, -4, 4, 4)
                .contains(event.position().toPoint())
            ):
                self.tick_clicked.emit()
                return  # (the box is not a click on the heading)
        super().mousePressEvent(event)
