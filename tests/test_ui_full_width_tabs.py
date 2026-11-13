# ui/full_width_tabs.py (ported from AKAISDS and jv-studio): the tabs of a QTabWidget share its whole width, to the pixel, and keep doing so after a resize.

from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from dubcrate.ui.full_width_tabs import FullWidthTabBar
from dubcrate.ui.settings_dialog import SettingsDialog


def _tabs(count, width):
    tabs = QTabWidget()
    tabs.setTabBar(FullWidthTabBar(tabs))
    for i in range(count):
        tabs.addTab(QWidget(), f"Tab {i}")
    tabs.resize(width, 200)
    tabs.show()
    QApplication.processEvents()
    return tabs


def _widths(tabs):
    bar = tabs.tabBar()
    return [bar.tabRect(i).width() for i in range(bar.count())]


def test_tabs_fill_the_width_and_leftover_pixels_go_to_the_last_tabs(qapp):
    tabs = _tabs(3, 310)  # 310 / 3 is not whole
    widths = _widths(tabs)
    assert sum(widths) == 310
    assert widths[0] == widths[1] and widths[2] == widths[0] + 1


def test_tabs_still_fill_the_width_after_a_resize(qapp):
    tabs = _tabs(3, 310)
    tabs.resize(400, 200)
    QApplication.processEvents()
    assert sum(_widths(tabs)) == 400


def test_a_hidden_tab_does_not_take_a_share(qapp):
    tabs = _tabs(4, 400)
    tabs.setTabVisible(3, False)
    tabs.resize(399, 200)
    QApplication.processEvents()
    assert sum(w for i, w in enumerate(_widths(tabs)) if tabs.isTabVisible(i)) == 399


def test_settings_tabs_match_the_pane_width(qapp):
    dialog = SettingsDialog(device_factory=lambda *a: None)
    dialog.show()
    QApplication.processEvents()
    tabs = dialog.findChild(QTabWidget)
    assert sum(_widths(tabs)) == tabs.width()
    assert tabs.tabBar().width() == tabs.width()
