"""Layout checks for the UI snapshot harness (scripts/ui_snapshots.py).

A screenshot shows what a widget looks like; these checks find what a screenshot is bad at
showing: a label a few pixels too narrow for its text, a placeholder that is cut off, a wrapped
paragraph with too little height, a layout squeezed below its minimum size. They run on a widget
that has been shown and laid out, and return a list of Issues. They are heuristics: each issue is
"worth a look", not proof of a bug, so the kinds are conservative and each says what was measured.

`collect_texts` is the other half: every piece of text a widget shows or hides in a tooltip, so
wording can be proofread without squinting at pictures (and tooltips can be read at all).
"""

from dataclasses import dataclass, field, replace

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTabBar,
    QWidget,
)

SLACK = 2  # pixels of difference below which text "fits" (rounding, anti-aliasing)
FIELD_PADDING = 20  # frame + margins of a line edit
COMBO_ARROW = 28  # a combo box's arrow and padding
MAX_TEXT = 400  # a longer piece of text is cut in the inventory (the image still shows it whole)

TEXT_CLIPPED = "text clipped"
SQUEEZED = "squeezed"
OUTSIDE_PARENT = "outside parent"
TABS_OVERFLOW = "tabs overflow"
SCROLLBAR = "scrollbar shown"


@dataclass(frozen=True)
class Issue:
    kind: str
    path: str  # where in the widget tree, eg "Dashboard > QLabel('Quantity:')"
    detail: str  # what was measured
    rect: tuple = field(default=(), compare=False)  # x, y, w, h within the window

    def key(self):
        # the same problem at different sizes/themes is one problem: this identifies it
        return (self.kind, self.path)


def _describe(widget):
    text = ""
    for getter in ("text", "placeholderText", "currentText"):
        if hasattr(widget, getter):
            try:
                text = getattr(widget, getter)() or ""
            except TypeError:
                text = ""
            if text:
                break
    text = text.replace("\n", " ")
    name = type(widget).__name__
    if widget.objectName():
        name += f"#{widget.objectName()}"
    return f"{name}({text[:28]!r})" if text else name


def _path(widget, root):
    parts = [_describe(widget)]
    parent = widget.parentWidget()
    while parent is not None and len(parts) < 3:
        parts.append(
            type(parent).__name__
            + (f"#{parent.objectName()}" if parent.objectName() else "")
        )
        if parent is root:
            break
        parent = parent.parentWidget()
    return " < ".join(parts)


def _inside_scroll_content(widget, root):
    # widgets inside a scroll area's content may legitimately run past the viewport
    parent = widget.parentWidget()
    while parent is not None and parent is not root:
        if (
            isinstance(parent, QScrollArea)
            or parent.objectName() == "qt_scrollarea_viewport"
        ):
            return True
        parent = parent.parentWidget()
    return False


def _check_text(widget, root, issues):
    fm = widget.fontMetrics()
    width = widget.width()
    if isinstance(widget, QLabel):
        if not widget.pixmap().isNull() or widget.movie() is not None:
            return
        text = widget.text()
        if (
            not text or "<" in text and ">" in text
        ):  # rich text: can't measure it simply
            return
        margins = widget.contentsMargins()
        room = width - margins.left() - margins.right() - 2 * widget.margin()
        if widget.wordWrap():
            # a wrapped paragraph needs its height: too short and its last lines are cut off
            if widget.hasHeightForWidth() and widget.heightForWidth(width) > widget.height() + SLACK:  # fmt: skip
                issues.append(
                    Issue(
                        TEXT_CLIPPED,
                        _path(widget, root),
                        f"wrapped text needs {widget.heightForWidth(width)}px of height at {width}px wide,"
                        f" the label is {widget.height()}px tall",
                    )
                )
            return
        if getattr(widget, "elides_text", False):  # cut off on purpose, with a tooltip
            return
        needed = max(fm.horizontalAdvance(line) for line in text.split("\n"))
        if needed > room + SLACK:
            issues.append(
                Issue(
                    TEXT_CLIPPED,
                    _path(widget, root),
                    f"text needs {needed}px, the label has {room}px",
                )
            )
    elif isinstance(widget, QPushButton | QCheckBox | QRadioButton):
        text = widget.text().replace("&", "")
        if not text:
            return
        # the style knows what the text, indicator and margins need: a control narrower than its size hint cuts its label
        needed = widget.sizeHint().width()
        if needed > width + SLACK:
            issues.append(
                Issue(
                    TEXT_CLIPPED,
                    _path(widget, root),
                    f"label needs about {needed}px, the control is {width}px wide",
                )
            )
    elif isinstance(widget, QLineEdit):
        text = widget.text() or widget.placeholderText()
        if widget.text() and widget.text() in widget.toolTip():
            return  # too long for the box, and says so on hover: deliberate
        if text and widget.echoMode() == QLineEdit.EchoMode.Normal:
            needed = fm.horizontalAdvance(text) + FIELD_PADDING
            if needed > width + SLACK:
                what = "text" if widget.text() else "placeholder"
                issues.append(
                    Issue(
                        TEXT_CLIPPED,
                        _path(widget, root),
                        f"{what} needs about {needed}px, the field is {width}px wide",
                    )
                )
    elif isinstance(widget, QComboBox):
        text = widget.currentText()
        if text:
            needed = fm.horizontalAdvance(text) + COMBO_ARROW
            if needed > width + SLACK:
                issues.append(
                    Issue(
                        TEXT_CLIPPED,
                        _path(widget, root),
                        f"'{text[:20]}' needs about {needed}px, the box is {width}px wide",
                    )
                )


def _check_geometry(widget, root, issues):
    if isinstance(widget, QAbstractScrollArea):
        bar = widget.horizontalScrollBar()
        if bar.isVisible() and bar.maximum() > 0:
            issues.append(
                Issue(
                    SCROLLBAR,
                    _path(widget, root),
                    "a horizontal scrollbar is showing: content is wider than the view",
                )
            )
        return
    if isinstance(widget, QTabBar):
        if widget.sizeHint().width() > widget.width() + SLACK:
            issues.append(
                Issue(
                    TABS_OVERFLOW,
                    _path(widget, root),
                    f"tabs need {widget.sizeHint().width()}px, the bar is {widget.width()}px",
                )
            )
        return

    hint = widget.minimumSizeHint()
    if (
        hint.isValid()
        and widget.layout() is not None
        and not _inside_scroll_content(widget, root)
        and (
            widget.width() < hint.width() - SLACK
            or widget.height() < hint.height() - SLACK
        )
    ):
        issues.append(
            Issue(
                SQUEEZED,
                _path(widget, root),
                f"is {widget.width()}x{widget.height()} but its contents need at least {hint.width()}x{hint.height()}",
            )
        )

    parent = widget.parentWidget()
    if (
        parent is not None
        and widget is not root
        and not widget.isWindow()
        and not _inside_scroll_content(widget, root)
        and parent.layout() is not None
    ):
        outside_right = widget.geometry().right() - parent.rect().right()
        outside_bottom = widget.geometry().bottom() - parent.rect().bottom()
        if outside_right > SLACK or outside_bottom > SLACK:
            issues.append(
                Issue(
                    OUTSIDE_PARENT,
                    _path(widget, root),
                    f"runs {max(outside_right, 0)}px past its parent's right edge and {max(outside_bottom, 0)}px past the bottom",
                )
            )


def find_layout_issues(root):
    """Issues found in `root` and everything visible inside it (shown and laid out)."""
    issues = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            continue
        before = len(issues)
        _check_text(widget, root, issues)
        _check_geometry(widget, root, issues)
        if len(issues) > before:
            origin = widget.mapTo(root, QPoint(0, 0))
            rect = (origin.x(), origin.y(), widget.width(), widget.height())
            issues[before:] = [replace(i, rect=rect) for i in issues[before:]]
    return issues


# -- the words on a screen --------------------------------------------------------------------------


def _clip(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def collect_texts(root):
    """Every text `root` shows or hides in a tooltip, as [{"kind", "text", "tip"}] in reading order
    (top to bottom, left to right). Only widgets that are visible; a tab that is not the current one is
    not, so a scene shows each tab it wants read. Rich text is left as written."""
    found = []
    seen = set()

    def add(kind, text, tip, widget):
        text, tip = _clip(text), _clip(tip)
        if not (text or tip):
            return
        key = (kind, text, tip)
        if key in seen:
            return
        seen.add(key)
        try:
            origin = widget.mapTo(root, QPoint(0, 0))
            order = (origin.y(), origin.x())
        except RuntimeError:
            order = (0, 0)
        found.append((order, {"kind": kind, "text": text, "tip": tip}))

    for widget in [root, *root.findChildren(QWidget)]:
        if not widget.isVisible() and widget is not root:
            continue
        tip = widget.toolTip() or ""
        if isinstance(widget, QLabel):
            add("label", widget.text() if widget.pixmap().isNull() else "", tip, widget)
        elif isinstance(widget, QAbstractButton):
            add("button", widget.text().replace("&", ""), tip, widget)
        elif isinstance(widget, QGroupBox):
            add("group", widget.title(), tip, widget)
        elif isinstance(widget, QLineEdit):
            add("field", widget.placeholderText(), tip, widget)
        elif isinstance(widget, QComboBox):
            items = ", ".join(widget.itemText(i) for i in range(widget.count()))
            add(
                "choice",
                f"{widget.currentText()}  [{items}]" if items else "",
                tip,
                widget,
            )
        elif isinstance(widget, QTabBar):
            for i in range(widget.count()):
                add("tab", widget.tabText(i), widget.tabToolTip(i), widget)
        elif isinstance(widget, QMenu):
            for action in widget.actions():
                add("menu item", action.text().replace("&", ""), action.toolTip() if action.toolTip() != action.text() else "", widget)  # fmt: skip
        elif tip:
            add("tooltip", "", tip, widget)
    if root.windowTitle():
        found.append(
            ((-1, -1), {"kind": "title", "text": _clip(root.windowTitle()), "tip": ""})
        )
    found.sort(key=lambda pair: pair[0])
    return [item for _order, item in found]
