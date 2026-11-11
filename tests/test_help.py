"""The user guide: its links work, and what it says about the app is true."""

import re
from pathlib import Path

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMenu, QMessageBox

from dubcrate.library import Library
from dubcrate.ui.main_window import MainWindow

MANUAL_PATH = Path(__file__).resolve().parent.parent / "src/dubcrate/ui/help/manual.md"
MANUAL = MANUAL_PATH.read_text(encoding="utf-8")


def heading_slug(text: str) -> str:
    """GitHub's anchor for a heading, which is what a Qt markdown viewer's links need."""
    return re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")


@pytest.fixture
def window(qapp, monkeypatch):
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    lib = Library.open(":memory:")
    win = MainWindow(lib)
    yield win
    win.close()
    win.deleteLater()
    lib.close()


def test_every_link_inside_the_guide_goes_to_a_heading():
    headings = {
        heading_slug(m.group(1).strip())
        for m in re.finditer(r"^#{1,6} (.+)$", MANUAL, re.MULTILINE)
    }
    links = re.findall(r"\]\(#([^)]+)\)", MANUAL)
    assert links, "the contents list has no links"
    assert [slug for slug in links if slug not in headings] == []


def test_every_section_is_in_the_contents():
    contents = set(re.findall(r"\]\(#([^)]+)\)", MANUAL))
    sections = {
        heading_slug(m.group(1).strip())
        for m in re.finditer(r"^## (.+)$", MANUAL, re.MULTILINE)
    } - {"contents"}
    assert sections - contents == set()


def test_every_image_the_guide_shows_exists():
    for name in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", MANUAL):
        assert (MANUAL_PATH.parent / name).is_file(), name


def test_menu_items_the_guide_names_exist(window):
    labels = {
        action.text().replace("&", "")
        for menu in window.menuBar().findChildren(QMenu)
        for action in menu.actions()
    }
    for item in (
        "Import Folder…",
        "Import rekordbox Library…",
        "Re-import Selected Source",
        "Remove Selected Source…",
        "Settings…",
        "Split Shown Tracks Across Discs…",
    ):
        assert item in labels, item
        assert item in MANUAL, item


def test_the_shortcuts_the_guide_lists_are_the_real_ones(window):
    shortcuts = {
        action.text().replace("&", ""): action.shortcut().toString(
            QKeySequence.SequenceFormat.PortableText
        )
        for menu in window.menuBar().findChildren(QMenu)
        for action in menu.actions()
        if not action.shortcut().isEmpty()
    }
    assert shortcuts["Import Folder…"] == "Ctrl+O"
    assert shortcuts["Import rekordbox Library…"] == "Ctrl+Shift+O"
    assert shortcuts["Re-import Selected Source"] == "Ctrl+R"
    assert shortcuts["New Disc…"] == "Ctrl+N"
    assert shortcuts["Add Selected Tracks"] == "Ctrl+D"
    assert shortcuts["Split Shown Tracks Across Discs…"] == "Ctrl+Shift+D"
    assert shortcuts["Settings…"] == "Ctrl+,"
    for keys in ("⌘O", "⇧⌘O", "⌘R", "⌘N", "⌘D", "⇧⌘D", "⌘,"):
        assert keys in MANUAL, keys


def test_the_buttons_the_guide_names_exist(window):
    assert window.add_button.text() == "Add to Disc"
    assert window.fit_button.text() == "Fit All to Disc"
    panel = window.disc_panel
    assert [panel.tabs.tabText(i) for i in range(panel.tabs.count())] == [
        "Planned",
        "Burned",
    ]
    assert panel.duplicate_button.text() == "Duplicate"
    assert panel.delete_burn_button.text() == "Delete"
    assert panel.remove_button.text() == "Remove"
    for name in ("Add to Disc", "Fit All to Disc", "Duplicate", "Planned", "Burned"):
        assert name in MANUAL, name


def test_the_settings_tabs_the_guide_names_exist(qapp):
    from PySide6.QtWidgets import QTabWidget

    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    tabs = dialog.findChild(QTabWidget)
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert names == ["Audio", "Discs", "Appearance"]
    for name in names:
        assert f"**{name}:**" in MANUAL, name
    assert "Plan new discs for" in MANUAL and "Between tracks" in MANUAL
    assert dialog.minutes_combo.currentData() == 80  # "80 minutes ... gapless"
