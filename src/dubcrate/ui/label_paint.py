"""Painting index labels onto a page: the print window's preview, a printer, or a PDF.

One painter does all three (`draw_page`), so what the preview shows is what comes out of the
printer. It works in device pixels, converting millimetres by the device's resolution. It never
reads the theme: labels are black on white whatever the window looks like, in the system's own
font (the System 6 theme's pixel font would print badly).
"""

from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPagedPaintDevice,
    QPageLayout,
    QPageSize,
    QPaintDevice,
    QPainter,
    QPen,
)

from dubcrate.labels import (
    HEADING_ROWS,
    PADDING_MM,
    ROW_SPACING,
    LabelOptions,
    Placed,
    column_widths,
)

INK = QColor(0, 0, 0)
SOFT_INK = QColor(80, 80, 80)  # artist, BPM and key: there, but the title leads
OUTLINE_MM = 0.15


PAGE_IDS = {
    "A3": QPageSize.PageSizeId.A3,
    "A4": QPageSize.PageSizeId.A4,
    "A5": QPageSize.PageSizeId.A5,
    "A6": QPageSize.PageSizeId.A6,
    "Letter": QPageSize.PageSizeId.Letter,
    "Legal": QPageSize.PageSizeId.Legal,
}


def page_layout(options: LabelOptions) -> QPageLayout:
    """The chosen paper, portrait, with no margins: the options place everything themselves."""
    return QPageLayout(
        QPageSize(PAGE_IDS[options.page]),
        QPageLayout.Orientation.Portrait,
        QMarginsF(0, 0, 0, 0),
    )


def _font(mm: float, bold: bool = False) -> QFont:
    font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
    font.setPointSizeF(mm * 72 / 25.4)
    font.setBold(bold)
    return font


def draw_page(
    painter: QPainter, device: QPaintDevice, placed: list[Placed], options: LabelOptions
) -> None:
    """Draw the labels of one page. The painter must be active on `device`."""
    k = device.logicalDpiX() / 25.4  # device pixels in a millimetre
    for item in placed:
        _draw_label(painter, device, k, item, options)


def _draw_label(
    painter: QPainter, device: QPaintDevice, k: float, item: Placed, o: LabelOptions
) -> None:
    label = item.label
    rect = QRectF(item.x_mm * k, item.y_mm * k, o.width_mm * k, o.height_mm * k)
    painter.save()
    painter.setClipRect(
        rect.adjusted(-k, -k, k, k)
    )  # text never spills onto a neighbour
    if o.outline:
        painter.setPen(QPen(SOFT_INK, OUTLINE_MM * k))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
    painter.setClipRect(rect)

    text = _font(label.font_mm)
    bold = _font(label.font_mm * 1.1, bold=True)
    fm = QFontMetricsF(text, device)
    pad = PADDING_MM * k
    left, right = rect.left() + pad, rect.right() - pad
    gap = label.font_mm * 0.7 * k
    row_h = label.font_mm * ROW_SPACING * k
    head_h = label.font_mm * HEADING_ROWS * k

    def width(strings) -> float:
        return max((fm.horizontalAdvance(s) for s in strings), default=0.0)

    number_w = width(str(row.number) for row in label.rows)
    bpm_w = max(width(row.bpm for row in label.rows), fm.horizontalAdvance("BPM"))
    key_w = max(width(row.key for row in label.rows), fm.horizontalAdvance("Key"))
    bpm_w = bpm_w if label.has_bpm else 0.0
    key_w = key_w if label.has_key else 0.0

    # columns, left to right: number, title, artist ... bpm, key (these two flush right)
    right_cols = (key_w + gap if key_w else 0.0) + (bpm_w + gap if bpm_w else 0.0)
    text_room = right - left - (number_w + gap) - right_cols - gap
    title_w, artist_w = column_widths(
        width(row.title for row in label.rows),
        width(row.artist for row in label.rows),
        max(text_room, 0.0),
    )
    number_x = left
    title_x = left + number_w + gap
    artist_x = title_x + title_w + gap
    key_x = right - key_w
    bpm_x = key_x - (gap if key_w else 0.0) - bpm_w

    def put(
        x: float, y: float, w: float, h: float, s: str, align=Qt.AlignmentFlag.AlignLeft
    ):
        if s and w > 0:
            shown = fm.elidedText(s, Qt.TextElideMode.ElideRight, w)
            painter.drawText(
                QRectF(x, y, w, h), align | Qt.AlignmentFlag.AlignVCenter, shown
            )

    top = rect.top() + pad
    painter.setPen(INK)
    painter.setFont(bold)
    painter.drawText(
        QRectF(left, top, right - left, head_h),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        QFontMetricsF(bold, device).elidedText(
            label.heading, Qt.TextElideMode.ElideRight, right - left
        ),
    )
    painter.setFont(text)
    painter.setPen(SOFT_INK)
    right_align = Qt.AlignmentFlag.AlignRight
    if bpm_w:
        put(bpm_x, top, bpm_w, head_h, "BPM", right_align)
    if key_w:
        put(key_x, top, key_w, head_h, "Key", right_align)
    painter.setPen(QPen(SOFT_INK, OUTLINE_MM * k))
    painter.drawLine(
        int(left), int(top + head_h * 0.95), int(right), int(top + head_h * 0.95)
    )

    y = top + head_h
    for row in label.rows:
        painter.setPen(SOFT_INK)
        put(number_x, y, number_w, row_h, str(row.number), right_align)
        painter.setPen(INK)
        put(title_x, y, title_w, row_h, row.title)
        painter.setPen(SOFT_INK)
        put(artist_x, y, artist_w, row_h, row.artist)
        put(bpm_x, y, bpm_w, row_h, row.bpm, right_align)
        put(key_x, y, key_w, row_h, row.key, right_align)
        y += row_h
    painter.restore()


def write_pages(
    device: QPagedPaintDevice, pages: list[list[Placed]], options: LabelOptions
) -> bool:
    """Paint every page onto a printer or PDF writer (already set to the paper, see `page_layout`). False if it can't start."""
    painter = QPainter()
    if not painter.begin(device):
        return False
    try:
        for i, page in enumerate(pages):
            if i:
                device.newPage()
            draw_page(painter, device, page, options)
    finally:
        painter.end()
    return True


def page_image(page: list[Placed], options: LabelOptions, dpi: int = 110) -> QImage:
    """One page as a picture, for the preview."""
    page_w, page_h = options.page_mm
    image = QImage(
        round(page_w * dpi / 25.4),
        round(page_h * dpi / 25.4),
        QImage.Format.Format_RGB32,
    )
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_page(painter, image, page, options)
    painter.setPen(QColor(160, 160, 160))  # the page's edge, as paper on a white window
    painter.drawRect(image.rect().adjusted(0, 0, -1, -1))
    painter.end()
    return image
