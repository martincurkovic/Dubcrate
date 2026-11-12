"""The label window's page preview: one page, sharp at any zoom, scrollable when zoomed in.

It paints only what is on screen, at the screen's real pixel density, through the same drawing
code as the printer (`label_paint`), so zooming in shows what the printer will put on the paper
instead of a bigger blurry picture. Zoom 1.0 is the page at roughly its real size on screen
(`BASE_DPI`); 0.0 means "fit the page to the pane". Ctrl + wheel steps through `ZOOM_STEPS` and a
trackpad pinch zooms smoothly (`MIN_ZOOM` to `MAX_ZOOM`); both keep the point under the pointer
where it is, and tell the window through `zoom_changed` so its zoom box follows.
"""

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QAbstractScrollArea

from dubcrate.labels import LabelOptions, Placed
from dubcrate.ui import label_paint

BASE_DPI = 96  # zoom 1.0: a screen's usual idea of an inch
FIT = 0.0
ZOOM_STEPS = (1.0, 1.5, 2.0, 3.0, 4.0)
MIN_ZOOM = 0.25
MAX_ZOOM = ZOOM_STEPS[-1]
MARGIN = 8  # round the page when it is fitted, in pixels


class PagePreview(QAbstractScrollArea):
    zoom_changed = Signal(
        float
    )  # the user zoomed (wheel or pinch): FIT, or the new factor

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page: list[Placed] = []
        self._options = LabelOptions()
        self._zoom = FIT
        self._drag_from: QPoint | None = None
        self.setMinimumSize(280, 400)
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.viewport().setMouseTracking(True)  # for the hand cursor
        self.horizontalScrollBar().valueChanged.connect(self.viewport().update)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)

    # -- what is shown --------------------------------------------------

    def set_page(self, page: list[Placed], options: LabelOptions) -> None:
        self._page = page
        self._options = options
        self._update_scrollbars()
        self.viewport().update()

    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """FIT, or a factor (1.0 is about real size). With `anchor`, a point of the viewport, the
        part of the page under it stays under it, as it does when zooming with the fingers."""
        if anchor is not None:
            origin, content = self._origin(), self.content_size()
            fx = (anchor.x() - origin.x()) / max(content.width(), 1)
            fy = (anchor.y() - origin.y()) / max(content.height(), 1)
        self._zoom = zoom
        self._update_scrollbars()
        if anchor is not None:
            content, view = self.content_size(), self.viewport().size()
            if content.width() > view.width():
                self.horizontalScrollBar().setValue(
                    round(fx * content.width() - anchor.x())
                )
            if content.height() > view.height():
                self.verticalScrollBar().setValue(
                    round(fy * content.height() - anchor.y())
                )
        self.viewport().update()

    def _zoom_by_user(self, zoom: float, anchor: QPointF) -> None:
        if zoom != FIT:
            zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self.set_zoom(zoom, anchor)
        self.zoom_changed.emit(zoom)

    def zoom(self) -> float:
        """The factor in use: what was asked for, or what makes the page fit."""
        if self._zoom != FIT:
            return self._zoom
        page_w, page_h = self._options.page_mm
        room = self.viewport().size()
        return max(
            0.1,
            min(
                (room.width() - 2 * MARGIN) * 25.4 / (page_w * BASE_DPI),
                (room.height() - 2 * MARGIN) * 25.4 / (page_h * BASE_DPI),
            ),
        )

    def _ratio(self) -> float:
        return self.devicePixelRatioF()

    def _device_dpi(self) -> int:
        """Pixels per inch on the real screen at this zoom, a whole number so the drawing's own
        millimetre scale and the layout here agree."""
        return max(1, round(BASE_DPI * self.zoom() * self._ratio()))

    def content_size(self) -> QSize:
        """The page's size at this zoom, in the widget's (logical) pixels."""
        page_w, page_h = self._options.page_mm
        dpi, ratio = self._device_dpi(), self._ratio()
        return QSize(
            round(page_w * dpi / 25.4 / ratio), round(page_h * dpi / 25.4 / ratio)
        )

    def _update_scrollbars(self) -> None:
        content, view = self.content_size(), self.viewport().size()
        bars = (
            (self.horizontalScrollBar(), content.width(), view.width()),
            (self.verticalScrollBar(), content.height(), view.height()),
        )
        for bar, whole, shown in bars:
            bar.setRange(0, max(0, whole - shown))
            bar.setPageStep(shown)
            bar.setSingleStep(max(1, shown // 10))

    def _origin(self) -> QPoint:
        """Where the page's top-left corner is on the viewport: centred if it is smaller."""
        content, view = self.content_size(), self.viewport().size()
        x = (
            (view.width() - content.width()) // 2
            if content.width() <= view.width()
            else -self.horizontalScrollBar().value()
        )
        y = (
            (view.height() - content.height()) // 2
            if content.height() <= view.height()
            else -self.verticalScrollBar().value()
        )
        return QPoint(x, y)

    # -- painting -------------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self.palette().window())
        origin, content = self._origin(), self.content_size()
        visible = QRect(origin, content).intersected(self.viewport().rect())
        if visible.isEmpty():
            return
        ratio = self._ratio()
        wanted = visible.translated(
            -origin
        )  # the part of the page to draw, in logical pixels
        region = QRect(
            round(wanted.x() * ratio),
            round(wanted.y() * ratio),
            round(wanted.width() * ratio),
            round(wanted.height() * ratio),
        )
        image = label_paint.page_image(
            self._page, self._options, self._device_dpi(), region
        )
        image.setDevicePixelRatio(ratio)
        painter.drawImage(visible.topLeft(), image)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    # -- the mouse ------------------------------------------------------

    def wheelEvent(self, event) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        now = self.zoom()
        if event.angleDelta().y() > 0:
            bigger = [z for z in ZOOM_STEPS if z > now + 1e-6]
            self._zoom_by_user(
                bigger[0] if bigger else ZOOM_STEPS[-1], event.position()
            )
        elif event.angleDelta().y() < 0:
            smaller = [z for z in ZOOM_STEPS if z < now - 1e-6]
            self._zoom_by_user(smaller[-1] if smaller else FIT, event.position())
        event.accept()

    def viewportEvent(self, event) -> bool:
        """A trackpad pinch arrives as a native gesture on the viewport; each event carries the
        change since the last (0.02 is two per cent more)."""
        if (
            event.type() == QEvent.Type.NativeGesture
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self._zoom_by_user(self.zoom() * (1 + event.value()), event.position())
            event.accept()
            return True
        return super().viewportEvent(event)

    def _scrollable(self) -> bool:
        return (
            self.horizontalScrollBar().maximum() > 0
            or self.verticalScrollBar().maximum() > 0
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._scrollable():
            self._drag_from = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            here = event.position().toPoint()
            moved = here - self._drag_from
            self._drag_from = here
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - moved.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - moved.y()
            )
        else:
            cursor = (
                Qt.CursorShape.OpenHandCursor
                if self._scrollable()
                else Qt.CursorShape.ArrowCursor
            )
            self.viewport().setCursor(cursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_from is not None:
            self._drag_from = None
            self.mouseMoveEvent(event)  # (back to the open hand)
