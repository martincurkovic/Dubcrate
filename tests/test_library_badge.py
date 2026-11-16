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
