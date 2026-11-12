"""The user guide: its links work, and what it says about the app is true."""

import re
from pathlib import Path

import pytest
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QMenu, QMessageBox

from dubcrate.library import Library
from dubcrate.ui.help_dialog import heading_slug
from dubcrate.ui.main_window import MainWindow

MANUAL_PATH = Path(__file__).resolve().parent.parent / "src/dubcrate/ui/help/manual.md"
MANUAL = MANUAL_PATH.read_text(encoding="utf-8")


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
        "Burn Disc…",
        "Print Labels…",
        "User Guide",
    ):
        assert item in labels, item
        assert item in MANUAL, item
    assert "Waveform" in labels  # View > Waveform
    assert "View > Waveform" in MANUAL


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
    assert shortcuts["Re-import Selected Source"] == "Ctrl+Shift+R"
    assert shortcuts["Rename"] == "Ctrl+R"
    assert shortcuts["Keyboard Shortcuts"] == "Ctrl+/"
    assert shortcuts["New Disc"] == "Ctrl+N"
    assert shortcuts["Add Selected Tracks"] == "Ctrl+Return"  # (⌘D works too)
    assert shortcuts["Split Shown Tracks Across Discs…"] == "Ctrl+Shift+D"
    assert shortcuts["Settings…"] == "Ctrl+,"
    assert shortcuts["Burn Disc…"] == "Ctrl+B"
    assert shortcuts["Print Labels…"] == "Ctrl+P"
    assert shortcuts["Find Tracks"] == "Ctrl+F"
    for keys in (
        "⌘O",
        "⇧⌘O",
        "⌘R",
        "⇧⌘R",
        "⌘⌫",
        "⌘/",
        "⌘N",
        "⌘D",
        "⇧⌘D",
        "⌘,",
        "⌘B",
        "⌘P",
        "⌘F",
    ):
        assert keys in MANUAL, keys


def test_the_buttons_the_guide_names_exist(window):
    assert window.add_button.text() == "Add to Disc"
    assert window.fit_button.text() == "Fit All to Disc"
    assert window.new_disc_button.text() == "New Disc"
    assert window.new_folder_button.text() == "New Folder"
    assert window.disc_view.remove_button.text() == "Remove"
    assert window.disc_view.burn_button.text() == "Burn…"
    for name in ("Add to Disc", "Fit All to Disc", "New Disc", "New Folder", "Burn…"):
        assert name in MANUAL, name


def test_the_right_hand_pane_the_guide_describes_is_the_real_one(window):
    assert window.splitter.count() == 3  # library, the middle, the discs
    assert window.splitter.widget(2).isAncestorOf(window.disc_tree)
    for phrase in (
        "## Your discs",
        "**Discs** (right)",
        "Adding to",
        "deleting a folder deletes what is in it",
        "**double-clicking** its name",
        "The + button",
        "ready to type over",
        "A burned disc is just a disc",
        "Duplicate",
    ):
        assert phrase in MANUAL, phrase
    assert "Burned** view" not in MANUAL and "frozen" not in MANUAL.replace(
        "nothing is frozen", ""
    )
    menu = window.disc_tree_menu(
        __import__("PySide6.QtCore", fromlist=["QModelIndex"]).QModelIndex()
    )
    assert [a.text() for a in menu.actions()] == [
        "New Disc",
        "New MP3 Disc",
        "New Folder",
    ]


def test_the_settings_tabs_the_guide_names_exist(qapp):
    from PySide6.QtWidgets import QTabWidget

    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    tabs = dialog.findChild(QTabWidget)
    names = [tabs.tabText(i) for i in range(tabs.count())]
    assert names == ["Audio", "Discs", "Burning", "Library", "Appearance"]
    assert "Check for a new version when Dubcrate starts" in dialog.auto_update_check.text()  # fmt: skip
    assert "Check for a new version when Dubcrate starts" in MANUAL
    for name in names:
        assert f"**{name}:**" in MANUAL, name
    assert "Plan new discs for" in MANUAL and "Between tracks" in MANUAL
    assert dialog.minutes_combo.currentData() == 80  # "80 minutes ... gapless"
    assert "Burn speed" in MANUAL and dialog.speed_combo.currentData() == 10  # "10x"
    assert "Waveform style" in MANUAL and "Waveform background" in MANUAL
    assert dialog.wave_style_combo.itemText(0) == "3-Band"


def test_the_column_menu_item_the_guide_names_exists():
    assert "Reset Columns" in MANUAL
    source = (MANUAL_PATH.parent.parent / "main_window.py").read_text()
    assert '"Reset Columns"' in source


def test_the_scratch_and_beat_jump_the_guide_describes_exist():
    from dubcrate import settings
    from dubcrate.ui.track_table import TrackTable
    from dubcrate.ui.waveform_view import DetailView

    for phrase in ("Scratching", "Beat jump", "Settings > Audio", "← / →"):
        assert phrase in MANUAL, phrase
    assert hasattr(DetailView, "scratch_moved") and hasattr(
        TrackTable, "beat_jump_requested"
    )
    assert (
        settings.DEFAULT_BEAT_JUMP == 4 and "4 unless" in MANUAL
    )  # "4 unless you change it"


def test_the_extra_columns_the_guide_lists_are_the_real_ones():
    from dubcrate.ui.models import TRACK_COLUMNS

    extras = [c.title for c in TRACK_COLUMNS if not c.shown]
    assert "More Columns" in MANUAL and "Reset Columns" in MANUAL
    for title in ("Album", "Year", "Rating", "My Tags", "Label", "Remixer", "Composer",
                  "Comment", "Plays", "Date Added", "Bitrate", "Sample Rate", "Format", "Path"):  # fmt: skip
        assert title in extras, (
            title
        )  # a column the guide names exists, and is off by default
        assert title in MANUAL, title
    assert set(extras) == {
        "Album", "Year", "Rating", "My Tags", "Label", "Remixer", "Composer", "Comment",
        "Plays", "Date Added", "Bitrate", "Sample Rate", "Format", "Path",
    }  # and the guide doesn't miss one  # fmt: skip


def test_the_disc_list_the_guide_describes_is_the_real_one(qapp):
    from dubcrate.ui.models import DISC_COLUMNS

    assert DISC_COLUMNS == ("#", "Time", "Artist", "Title")
    assert "What goes on the CD" in MANUAL and "this disc only" in MANUAL
    for phrase in ("Double-click", "disc's name", "Duplicate", "CD-TEXT"):
        assert phrase in MANUAL, phrase
    assert (
        "BPM"
        not in MANUAL.split("**What goes on the CD**")[1].split("**Arranging**")[0]
    )


def test_the_fixed_width_columns_the_guide_mentions_are_fixed():
    from dubcrate.ui.models import TRACK_COLUMNS

    assert "fixed width" in MANUAL
    fixed = {c.title for c in TRACK_COLUMNS if c.fixed}
    assert {"", "Preview", "BPM", "Key", "Time"} <= fixed
    assert not {"Title", "Artist"} & fixed


def test_the_startup_update_the_guide_describes_is_real(qapp):
    from dubcrate import settings
    from dubcrate.ui.settings_dialog import SettingsDialog

    for phrase in ("keeps your rekordbox library up to date by itself", "--no-import", "splash screen",
                   "File > Settings… > Library", "never imports one on its own"):  # fmt: skip
        assert phrase in MANUAL, phrase
    assert settings.get_auto_import() is True  # "on by default"
    assert (
        SettingsDialog()
        .auto_import_check.text()
        .startswith("Update my rekordbox library")
    )


def test_the_help_menu_items_the_guide_names_exist(qapp):
    from dubcrate.library import Library
    from dubcrate.ui.main_window import MainWindow

    with Library.open(":memory:") as lib:
        window = MainWindow(lib)
        names = {a.text().replace("&", "") for a in window.menuBar().findChildren(QMenu)[-1].actions()}  # fmt: skip
        window.close()
    for item in (
        "User Guide",
        "About Dubcrate",
        "Third-Party Notices…",
        "Check for Updates…",
    ):
        assert item in names, item
        assert f"**Help > {item}**" in MANUAL or item == "User Guide" or item == "About Dubcrate", item  # fmt: skip
    for phrase in ("## Updates, and what Dubcrate sends", "Skip This Version", "Remind Me Later", "View Release…"):  # fmt: skip
        assert phrase in MANUAL, phrase


def test_the_guide_describes_the_label_window_as_it_is():
    from dubcrate.ui.label_dialog import LabelDialog

    for phrase in (
        "## Printing labels",
        "Print Labels…",
        "**Spread each disc over**",
        "**Columns (up to)**",
        "**Font size**",
        "**Copies of each**",
        "**Zoom**",
        "**pinching**",
        "**Folders on an MP3 disc**",
        "(cont.)",
    ):
        assert phrase in MANUAL, phrase
    assert "audio CDs and MP3 CDs" in MANUAL
    assert "Audio CDs only" not in MANUAL and "Only **audio CDs**" not in MANUAL
    assert LabelDialog  # (the window the section is about)


def test_the_guide_describes_the_explorer_tab(window):
    for phrase in (
        "## The Explorer",
        "**Explorer** tab",
        "**Library**",
        "never offers the computer's whole system disk",
        "not part of your Library",
        "Files & Folders",
    ):
        assert phrase in MANUAL, phrase
    assert "(#the-explorer)" in MANUAL
    assert window.source_tabs.keys() == ["library", "explorer"]
    labels = [b.text() for b in window.source_tabs._buttons.values()]
    assert labels == ["Library", "Explorer"]
