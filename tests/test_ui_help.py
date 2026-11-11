"""The User Guide window: it renders the guide, its links and search work."""

import re

import pytest
from PySide6.QtGui import QColor, QImage, QKeySequence, QTextTable
from PySide6.QtWidgets import QMessageBox, QPushButton

from dubcrate.library import Library
from dubcrate.ui import help_dialog, theme
from dubcrate.ui.help_dialog import HelpDialog, heading_slug, show_help_dialog
from dubcrate.ui.main_window import MainWindow

MANUAL = help_dialog.MANUAL_PATH.read_text(encoding="utf-8")


@pytest.fixture
def dialog(qapp):
    d = HelpDialog()
    yield d
    d.close()
    d.deleteLater()


def anchors(dialog) -> set[str]:
    return {
        name
        for fragment in help_dialog._fragments(dialog.viewer.document())
        for name in fragment.charFormat().anchorNames()
    }


# -- the page -----------------------------------------------------------------


def test_the_guide_is_rendered_not_shown_as_raw_markdown(dialog):
    assert dialog.loaded
    text = dialog.viewer.toPlainText()
    assert "Dubcrate User Guide" in text and "Planning a disc" in text
    assert "## " not in text and "**" not in text


def test_every_contents_link_has_a_heading_to_jump_to(dialog):
    links = set(re.findall(r"\]\(#([^)]+)\)", MANUAL))
    assert links
    assert links <= anchors(dialog)


def test_heading_slugs_match_githubs():
    assert heading_slug("Keyboard shortcuts") == "keyboard-shortcuts"
    assert heading_slug("Sending & Receiving") == "sending--receiving"  # "&" dropped
    assert heading_slug("Discs you have burned") == "discs-you-have-burned"


def test_callouts_are_drawn_as_boxes(dialog):
    boxes = [
        f
        for f in dialog.viewer.document().rootFrame().childFrames()
        if isinstance(f, QTextTable) and (f.rows(), f.columns()) == (1, 1)
    ]
    assert len(boxes) >= 2  # the burning warnings


def test_a_contents_link_scrolls_the_page_to_its_heading(dialog, qapp):
    dialog.show()
    qapp.processEvents()
    bar = dialog.viewer.verticalScrollBar()
    assert bar.value() == 0
    dialog.viewer.scrollToAnchor("troubleshooting")
    assert bar.value() > 0


def test_external_links_open_in_the_browser(dialog):
    assert dialog.viewer.openExternalLinks()


# -- find in page -------------------------------------------------------------


def test_searching_counts_matches_and_steps_through_them(dialog):
    dialog.show()
    dialog.open_search()
    assert dialog.search_bar.isVisible()
    dialog.search_input.setText("burn")
    total = len(dialog._matches)
    assert total > 3
    assert dialog.results_label.text() == f"1 of {total}"
    dialog.find_next()
    assert dialog.results_label.text() == f"2 of {total}"
    dialog.find_previous()
    dialog.find_previous()  # wraps to the last one
    assert dialog.results_label.text() == f"{total} of {total}"
    assert len(dialog.viewer.extraSelections()) == total


def test_a_search_with_no_matches_says_so_and_closing_clears_the_highlights(dialog):
    dialog.show()
    dialog.open_search()
    dialog.search_input.setText("burn")
    dialog.search_input.setText("zzzzzz")
    assert dialog.results_label.text() == "0 of 0"
    assert dialog.viewer.extraSelections() == []
    dialog.search_input.setText("burn")
    assert dialog.viewer.extraSelections()
    dialog.close_search()
    assert not dialog.search_bar.isVisible()
    assert dialog.viewer.extraSelections() == []


# -- themes -------------------------------------------------------------------


@pytest.mark.parametrize("preference", ["dark", "light", "system6"])
def test_the_guide_loads_and_restyles_in_every_theme(qapp, preference):
    theme.set_theme_preference(preference)
    d = HelpDialog()
    assert d.loaded and anchors(d)
    d.close()


def link_colours(dialog) -> set[str]:
    return {
        f.charFormat().foreground().color().name()
        for f in help_dialog._fragments(dialog.viewer.document())
        if f.charFormat().isAnchor() and f.charFormat().anchorHref()
    }


@pytest.mark.parametrize("preference", ["dark", "light", "system6"])
def test_links_are_drawn_in_the_themes_link_colour(qapp, preference):
    theme.set_theme_preference(preference)
    palette = theme.current_palette()
    expected = QColor(palette.get("link", palette["accent"])).name()
    d = HelpDialog()
    assert link_colours(d) == {expected}  # not Qt's default blue
    d.close()


def test_links_follow_a_theme_change_while_the_window_is_open(qapp):
    theme.set_theme_preference("light")
    d = HelpDialog()
    before = link_colours(d)
    theme.set_theme_preference("dark")
    after = link_colours(d)
    assert before != after and len(after) == 1
    d.close()


def test_no_button_in_the_search_bar_is_the_return_key_default(dialog):
    for button in dialog.search_bar.findChildren(QPushButton):
        assert not button.autoDefault() and not button.isDefault()


# -- files --------------------------------------------------------------------


def test_a_missing_guide_is_reported_in_the_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(help_dialog, "MANUAL_PATH", tmp_path / "nope.md")
    d = HelpDialog()
    assert not d.loaded
    assert "Couldn't load the user guide" in d.viewer.toPlainText()
    d.close()


def image_widths(dialog) -> list[float]:
    return [
        f.charFormat().toImageFormat().width()
        for f in help_dialog._fragments(dialog.viewer.document())
        if f.charFormat().isImageFormat()
    ]


def test_screenshots_shrink_to_fit_but_never_grow_past_their_size(
    qapp, tmp_path, monkeypatch
):
    image = QImage(600, 400, QImage.Format.Format_RGB32)  # a 2x capture: 300 x 200
    image.fill(QColor("red"))
    image.save(str(tmp_path / "pic.png"))
    (tmp_path / "manual.md").write_text("# T\n\n![a picture](pic.png)\n")
    monkeypatch.setattr(help_dialog, "HELP_DIR", tmp_path)
    monkeypatch.setattr(help_dialog, "MANUAL_PATH", tmp_path / "manual.md")
    d = HelpDialog()
    d._fit_images(1000)
    assert image_widths(d) == [300.0]  # not enlarged
    d._fit_images(120)
    assert image_widths(d) == [120.0]  # shrunk to fit
    d._fit_images(1000)
    assert image_widths(d) == [300.0]  # and back again
    d.close()


# -- from the main window -----------------------------------------------------


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    yield win
    if win._help_dialog is not None:
        win._help_dialog.close()
    win.close()
    win.deleteLater()
    lib.close()


def test_the_help_menu_opens_the_guide(window):
    assert window.act_help.text().replace("&", "") == "User Guide"
    assert window.act_help.shortcut() == QKeySequence(
        QKeySequence.StandardKey.HelpContents
    )
    window.act_help.trigger()
    assert window._help_dialog is not None and window._help_dialog.isVisible()


def test_choosing_the_guide_again_raises_the_open_one(window):
    window.act_help.trigger()
    first = window._help_dialog
    window.act_help.trigger()
    assert window._help_dialog is first
    assert show_help_dialog(window) is first
    assert len(window.findChildren(HelpDialog)) == 1
