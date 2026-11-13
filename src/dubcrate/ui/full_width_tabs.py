"""A tab bar whose tabs share the whole width of their QTabWidget (ported from jv-studio and AKAISDS).

QTabBar's own `expanding` only spreads the tabs over their natural width, so the bar is never stretched to the widget.
"""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QTabBar


class FullWidthTabBar(QTabBar):
    """Install with `tab_widget.setTabBar(FullWidthTabBar(tab_widget))` BEFORE adding tabs. It watches the QTabWidget's
    resize events, because the widget sizes its bar only once when the tabs are first laid out."""

    def __init__(self, tab_widget):
        super().__init__(tab_widget)
        tab_widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.resize(watched.width(), self.height())
        return super().eventFilter(watched, event)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        parent = self.parentWidget()
        visible = [
            i for i in range(self.count()) if self.isTabVisible(i)
        ]  # only visible tabs share the width
        if parent is not None and index in visible:
            count = len(visible)
            position = visible.index(index)
            base, remainder = divmod(parent.width(), count)
            # the leftover pixels go to the last tabs: the bar matches the pane exactly
            size.setWidth(base + (1 if position >= count - remainder else 0))
        return size
