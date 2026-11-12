"""Painting index labels onto a page: the print window's preview, a printer, or a PDF.

One painter does all three (`draw_page`), so what the preview shows is what comes out of the
printer. It works in device pixels, converting millimetres by the device's resolution. It never
reads the theme: labels are black on white whatever the window looks like, in the system's own
font (the System 6 theme's pixel font would print badly).
"""

from PySide6.QtCore import QMarginsF, QRect, QRectF, Qt
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
    FolderRow,
    LabelOptions,
    LabelRow,
    Placed,
    column_widths,
)

INK = QColor(0, 0, 0)
SOFT_INK = QColor(80, 80, 80)  # artist, BPM and key: there, but the title leads
OUTLINE_MM = 0.15
COLUMN_GAP_MM = 3.0  # between a label's columns
INLINE_TITLE_SHARE = (
    0.6  # in a one-line row the most the title keeps against a long artist
)


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
    folder_font = _font(label.font_mm, bold=True)
    fm = QFontMetricsF(text, device)
    pad = PADDING_MM * k
    left, right = rect.left() + pad, rect.right() - pad
    gap = label.font_mm * 0.7 * k
    row_h = label.font_mm * ROW_SPACING * k
    head_h = label.font_mm * HEADING_ROWS * k

    def width(strings) -> float:
        return max((fm.horizontalAdvance(s) for s in strings), default=0.0)

    def put(
        x: float, y: float, w: float, h: float, s: str, align=Qt.AlignmentFlag.AlignLeft
    ):
        if s and w > 0:
            shown = fm.elidedText(s, Qt.TextElideMode.ElideRight, w)
            painter.drawText(
                QRectF(x, y, w, h), align | Qt.AlignmentFlag.AlignVCenter, shown
            )

    right_align = Qt.AlignmentFlag.AlignRight
    # the optional columns at the right (BPM, key, time) are as wide as the label's widest, so
    # the columns of a label line up
    side = [
        (
            head,
            attr,
            max(
                width(getattr(r, attr) for r in label.rows), fm.horizontalAdvance(head)
            ),
        )
        for head, attr in label.side_columns
    ]
    side_total = sum(w + gap for _head, _attr, w in side)

    def side_places(edge: float) -> list[tuple[str, str, float, float]]:
        """(heading, attribute, left edge, width) of the optional columns ending at `edge`."""
        places, x = [], edge
        for head, attr, w in reversed(side):
            x -= w
            places.append((head, attr, x, w))
            x -= gap
        return places[::-1]

    count = len(label.columns)
    column_gap = COLUMN_GAP_MM * k
    column_w = (right - left - column_gap * (count - 1)) / count

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
    if count == 1:  # (headed columns would run into the next column's)
        for head, _attr, x, w in side_places(right):
            put(x, top, w, head_h, head, right_align)
    painter.setPen(QPen(SOFT_INK, OUTLINE_MM * k))
    painter.drawLine(
        int(left), int(top + head_h * 0.95), int(right), int(top + head_h * 0.95)
    )

    for c, items in enumerate(label.columns):
        x0 = left + c * (column_w + column_gap)
        tracks = [line for line in items if isinstance(line, LabelRow)]
        number_w = width(str(row.number) for row in tracks)
        x1 = x0 + column_w
        places = side_places(x1)
        text_x = x0 + number_w + gap
        text_room = max(x1 - side_total - gap - text_x, 0.0)
        if (
            count == 1
        ):  # title and artist in columns of their own, lined up down the label
            title_w, artist_w = column_widths(
                width(row.title for row in tracks),
                width(row.artist for row in tracks),
                text_room,
            )
            artist_x = text_x + title_w + gap

        y = top + head_h
        for line in items:
            if isinstance(line, FolderRow):
                painter.setFont(folder_font)
                painter.setPen(INK)
                shown = QFontMetricsF(folder_font, device).elidedText(
                    line.name, Qt.TextElideMode.ElideRight, column_w
                )
                painter.drawText(
                    QRectF(x0, y, column_w, row_h),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    shown,
                )
                painter.setPen(QPen(SOFT_INK, OUTLINE_MM * k * 0.6))
                painter.drawLine(
                    int(x0), int(y + row_h * 0.95), int(x1), int(y + row_h * 0.95)
                )
                painter.setFont(text)
                y += row_h
                continue
            painter.setPen(SOFT_INK)
            put(x0, y, number_w, row_h, str(line.number), right_align)
            painter.setPen(INK)
            if count == 1:
                put(text_x, y, title_w, row_h, line.title)
                painter.setPen(SOFT_INK)
                put(artist_x, y, artist_w, row_h, line.artist)
            else:  # "Title – Artist", each as long as it needs, the longer one trimmed
                dash = fm.horizontalAdvance(" – ") if line.artist else 0.0
                title_w, artist_w = column_widths(
                    fm.horizontalAdvance(line.title),
                    fm.horizontalAdvance(line.artist),
                    max(text_room - dash, 0.0),
                    INLINE_TITLE_SHARE,
                )
                put(text_x, y, title_w, row_h, line.title)
                painter.setPen(SOFT_INK)
                if line.artist:
                    put(
                        text_x + title_w, y, dash + artist_w, row_h, " – " + line.artist
                    )
            painter.setPen(SOFT_INK)
            for _head, attr, x, w in places:
                put(x, y, w, row_h, getattr(line, attr), right_align)
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


def page_image(
    page: list[Placed],
    options: LabelOptions,
    dpi: int = 110,
    region: QRect | None = None,
) -> QImage:
    """A page as a picture at `dpi` device pixels per inch, for the preview: all of it, or just
    `region` (a rectangle of the page in those pixels, so a zoomed preview draws only what shows)."""
    page_w, page_h = options.page_mm
    whole = QRect(0, 0, round(page_w * dpi / 25.4), round(page_h * dpi / 25.4))
    region = whole if region is None else region
    image = QImage(region.size(), QImage.Format.Format_RGB32)
    image.setDotsPerMeterX(round(dpi / 0.0254))
    image.setDotsPerMeterY(round(dpi / 0.0254))
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.translate(-region.x(), -region.y())
    draw_page(painter, image, page, options)
    painter.setPen(QColor(160, 160, 160))  # the page's edge, as paper on a window
    painter.drawRect(whole.adjusted(0, 0, -1, -1))
    painter.end()
    return image
