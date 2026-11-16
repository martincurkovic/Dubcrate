"""The status bar's badge for the rekordbox library: read-only (yellow) or writable (orange), only with a rekordbox library."""

import pytest

from dubcrate import settings
from dubcrate.library import Library, TrackRecord
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.status_bar import AppStatusBar


def rekordbox(lib):
    lib.replace_source(
        "rekordbox",
        "/rb/master.db",
        "rekordbox",
        [TrackRecord(ext_id="1", path="/m/a.mp3", title="A", duration=100.0)],
    )


def badges(bar: AppStatusBar) -> tuple[bool, bool]:
    return (
        bar.read_only_badge.isVisibleTo(bar),
        bar.writable_badge.isVisibleTo(bar),
    )


def test_the_badge_follows_what_the_status_bar_is_told(qapp):
    bar = AppStatusBar()
    assert badges(bar) == (False, False)
    bar.set_library_badge(False)
    assert badges(bar) == (True, False) and bar.read_only_badge.text() == "Library read-only"  # fmt: skip
    bar.set_library_badge(True)
    assert badges(bar) == (False, True) and bar.writable_badge.text() == "Library writable"  # fmt: skip
    bar.set_library_badge(None)
    assert badges(bar) == (False, False)


@pytest.fixture
def window(qapp):
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    yield win, lib
    win.deleteLater()
    lib.close()


def test_there_is_no_badge_until_a_rekordbox_library_is_imported(window):
    win, lib = window
    assert badges(win.statusBar()) == (False, False)
    rekordbox(lib)
    win.refresh_sources()
    assert badges(win.statusBar()) == (
        True,
        False,
    )  # (nothing chosen yet: Dubcrate does not write)


def test_the_badge_changes_when_saving_into_rekordbox_is_switched(window):
    win, lib = window
    rekordbox(lib)
    win.refresh_sources()
    settings.set_rekordbox_editing(True)
    assert badges(win.statusBar()) == (False, True)
    settings.set_rekordbox_editing(False)
    assert badges(win.statusBar()) == (True, False)


def test_removing_the_rekordbox_library_takes_the_badge_away(window):
    win, lib = window
    rekordbox(lib)
    win.refresh_sources()
    assert badges(win.statusBar()) == (True, False)
    lib.remove_source(lib.sources()[0].id)
    win.refresh_sources()
    assert badges(win.statusBar()) == (False, False)


def test_the_badge_colours_are_in_every_theme():
    from dubcrate.ui import theme

    keys = ("badge_ro_bg", "badge_ro_text", "badge_ro_border", "badge_rw_bg", "badge_rw_text", "badge_rw_border")  # fmt: skip
    for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE, theme.SYSTEM6_PALETTE):
        assert all(k in palette for k in keys)


def test_the_badge_is_in_the_bottom_right_corner_with_the_busy_bar_to_its_left(
    window, qapp
):
    from PySide6.QtCore import QPoint

    win, lib = window
    rekordbox(lib)
    win.refresh_sources()
    win.resize(1200, 700)
    win.show()
    bar = win.statusBar()
    win.busy_bar.show()  # (what is busy shares the corner's row)
    win.cancel_button.show()
    bar.update_button.show()  # (and so does the update button, at the other end)
    qapp.processEvents()
    corner = bar.badge_box
    assert corner.isVisible() and bar.read_only_badge.isVisible()
    assert corner.geometry().right() >= bar.width() - 12  # at the right edge
    assert win.busy_bar.geometry().right() < corner.geometry().left()
    assert win.cancel_button.geometry().right() < corner.geometry().left()
    update_right = bar.update_button.mapTo(
        bar, QPoint(bar.update_button.width(), 0)
    ).x()
    assert update_right < corner.geometry().left()
    version_left = bar.version_label.mapTo(bar, QPoint(0, 0)).x()
    assert (
        version_left < bar.update_button.mapTo(bar, QPoint(0, 0)).x()
    )  # (version, then the update button)


def test_there_is_no_gap_in_the_corner_without_a_rekordbox_library(window, qapp):
    win, _ = window
    win.show()
    qapp.processEvents()
    assert not win.statusBar().badge_box.isVisible()


@pytest.mark.parametrize("scheme", ["dark", "light", "system6"])
def test_nothing_in_the_status_bar_makes_a_line_beside_the_badge(window, qapp, scheme):
    """The gap Qt leaves between the message row and the badge is the same colour as both (it was darker: a line)."""
    from PySide6.QtCore import QPoint

    from dubcrate.ui import theme

    theme.apply_to_app(qapp, scheme)
    win, lib = window
    rekordbox(lib)
    win.refresh_sources()
    win.resize(1100, 700)
    win.show()
    qapp.processEvents()
    bar = win.statusBar()
    image = win.grab().toImage()
    row = next(w for w in bar.children() if w.isWidgetType() and w is not bar.badge_box)
    row_edge = row.mapTo(win, QPoint(row.width(), 0)).x()
    badge_left = bar.badge_box.mapTo(win, QPoint(0, 0)).x()
    assert badge_left - row_edge > 0  # (there is a gap between them)
    y = bar.mapTo(win, QPoint(0, bar.height() // 2)).y()
    colours = {image.pixelColor(x, y).name() for x in range(row_edge + 1, badge_left)}
    assert colours == {
        image.pixelColor(row_edge - 40, y).name()
    }  # (as the row beside it: no line)


@pytest.mark.parametrize("scheme", ["dark", "light", "system6"])
def test_the_status_bar_is_a_band_apart_from_the_app_with_a_rule_on_top(
    window, qapp, scheme
):
    from PySide6.QtCore import QPoint

    from dubcrate.ui import theme

    theme.apply_to_app(qapp, scheme)
    win, lib = window
    rekordbox(lib)
    win.refresh_sources()
    win.resize(1100, 700)
    win.show()
    qapp.processEvents()
    bar = win.statusBar()
    image = win.grab().toImage()
    top = bar.mapTo(win, QPoint(0, 0)).y()
    x = win.width() - 300  # (a bare spot in the bar, and the app straight above it)
    inside = image.pixelColor(x, top + bar.height() // 2).name()
    above = image.pixelColor(x, top - 8).name()
    rule = image.pixelColor(x, top).name()
    assert rule not in (inside, above)  # (a line between the app and the bar)
    if (
        scheme != "system6"
    ):  # (System 6 is white all over: its rule is the only divider)
        assert inside != above  # (the bar is a shade of its own)
    palette = theme.current_palette()
    assert inside.lower() == palette["bg_statusbar"].lower()
