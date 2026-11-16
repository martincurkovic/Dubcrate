"""The UI review gallery (scripts/ui_snapshots.py): its layout checks, its inventory of text, its HTML page and the list of scenes.

The runner itself needs the whole demo collection (about a minute to make), so it is run by hand; these tests cover
the parts that decide what the gallery says."""

import json
import re
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import ui_snapshot_checks as checks
import ui_snapshot_gallery as gallery
import ui_snapshot_scenes as scenes


def shown(widget, w=None, h=None):
    if w:
        widget.resize(w, h)
    widget.show()
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    return widget


def kinds(widget):
    return [(i.kind) for i in checks.find_layout_issues(widget)]


# -- the layout checks ----------------------------------------------------------------------------


def test_a_label_wider_than_its_box_is_flagged_and_one_with_room_is_not(qapp):
    host = QWidget()
    label = QLabel("Quantity needed", host)
    label.setGeometry(0, 0, 30, 20)
    assert kinds(shown(host, 300, 100)) == ["text clipped"]
    label.setGeometry(0, 0, 250, 20)
    assert kinds(host) == []


def test_a_wrapped_paragraph_with_too_little_height_is_flagged(qapp):
    host = QWidget()
    label = QLabel("word " * 60, host)
    label.setWordWrap(True)
    label.setGeometry(0, 0, 160, 18)
    assert kinds(shown(host, 200, 200)) == ["text clipped"]
    label.setGeometry(0, 0, 160, label.heightForWidth(160) + 4)
    assert kinds(host) == []


def test_a_button_narrower_than_its_label_is_flagged(qapp):
    host = QWidget()
    button = QPushButton("Erase and Format the Card", host)
    button.setGeometry(0, 0, 50, 30)
    assert kinds(shown(host, 200, 100)) == ["text clipped"]
    button.setGeometry(0, 0, button.sizeHint().width(), 30)
    assert kinds(host) == []


def test_a_checkbox_that_fits_is_not_flagged_by_a_pessimistic_estimate(qapp):
    host = QWidget()
    box = QCheckBox("Normalise", host)
    box.setGeometry(0, 0, box.sizeHint().width(), box.sizeHint().height())
    assert kinds(shown(host, 200, 100)) == []


def test_a_field_whose_placeholder_is_cut_off_is_flagged(qapp):
    host = QWidget()
    field = QLineEdit(host)
    field.setPlaceholderText("Select a formula preset to load")
    field.setGeometry(0, 0, 60, 24)
    assert kinds(shown(host, 200, 100)) == ["text clipped"]


def test_a_combo_whose_text_is_cut_off_is_flagged(qapp):
    host = QWidget()
    combo = QComboBox(host)
    combo.addItem("Smooth (60 a second)")
    combo.setGeometry(0, 0, 50, 24)
    assert kinds(shown(host, 200, 100)) == ["text clipped"]


def test_a_layout_squeezed_below_its_minimum_is_flagged(qapp):
    host = QWidget()
    inner = QWidget(host)
    layout = QVBoxLayout(inner)
    for text in ("one", "two", "three"):
        layout.addWidget(QPushButton(text))
    inner.setGeometry(0, 0, 200, 10)
    assert "squeezed" in kinds(shown(host, 200, 200))


def test_the_flag_says_where_the_widget_is_so_the_gallery_can_outline_it(qapp):
    host = QWidget()
    QLabel("A long piece of text that cannot fit", host).setGeometry(30, 40, 40, 20)
    [issue] = checks.find_layout_issues(shown(host, 200, 100))
    assert issue.rect == (30, 40, 40, 20)
    assert "QLabel" in issue.path


def test_the_same_problem_at_another_size_has_the_same_key(qapp):
    one = checks.Issue(
        "text clipped", "QLabel('x') < Dialog", "needs 90px", (0, 0, 5, 5)
    )
    two = checks.Issue(
        "text clipped", "QLabel('x') < Dialog", "needs 95px", (9, 9, 5, 5)
    )
    assert one.key() == two.key()


# -- the inventory of text ------------------------------------------------------------------------


def test_every_text_a_window_shows_or_hides_in_a_tooltip_is_listed(qapp):
    host = QWidget()
    host.setWindowTitle("Burn Disc")
    layout = QVBoxLayout(host)
    label = QLabel("Speed:")
    label.setToolTip("How fast the disc is written")
    layout.addWidget(label)
    layout.addWidget(QPushButton("&Burn"))
    field = QLineEdit()
    field.setPlaceholderText("Search…")
    layout.addWidget(field)
    combo = QComboBox()
    combo.addItems(["10x", "16x"])
    layout.addWidget(combo)
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Audio")
    tabs.setTabToolTip(0, "Where previews play")
    layout.addWidget(tabs)
    texts = checks.collect_texts(shown(host))
    seen = {(t["kind"], t["text"], t["tip"]) for t in texts}
    assert ("title", "Burn Disc", "") in seen
    assert ("label", "Speed:", "How fast the disc is written") in seen
    assert ("button", "Burn", "") in seen  # (the & is not text)
    assert ("field", "Search…", "") in seen
    assert any(k == "choice" and t.startswith("10x") and "16x" in t for k, t, _ in seen)
    assert ("tab", "Audio", "Where previews play") in seen


def test_text_is_listed_in_reading_order_and_each_once(qapp):
    host = QWidget()
    for y, text in ((60, "third"), (0, "first"), (30, "second"), (30, "second")):
        QLabel(text, host).move(0, y)
    shown(host, 100, 100)
    found = [t["text"] for t in checks.collect_texts(host) if t["kind"] == "label"]
    assert found == ["first", "second", "third"]


def test_what_is_not_shown_is_not_listed_and_a_menus_items_are(qapp):
    host = QWidget()
    QLabel("visible", host)
    QLabel("hidden", host).hide()
    texts = [t["text"] for t in checks.collect_texts(shown(host))]
    assert "visible" in texts and "hidden" not in texts
    menu = QMenu()
    menu.addAction("Rename")
    menu.addAction("Delete Folder…").setToolTip("Takes its tracks off the disc")
    menu.popup(host.mapToGlobal(host.rect().center()))
    items = {
        t["text"]: t["tip"]
        for t in checks.collect_texts(menu)
        if t["kind"] == "menu item"
    }
    assert items == {"Rename": "", "Delete Folder…": "Takes its tracks off the disc"}
    menu.close()


def test_a_very_long_text_is_cut_in_the_inventory_only(qapp):
    host = QWidget()
    QLabel("x" * 2000, host)
    [text] = [
        t["text"] for t in checks.collect_texts(shown(host)) if t["kind"] == "label"
    ]
    assert len(text) <= checks.MAX_TEXT + 1 and text.endswith("…")


# -- the page ------------------------------------------------------------------------------------


def manifest():
    return {
        "generated": "2026-11-16 22:00",
        "round": "1",
        "themes": ["dark"],
        "screens": [{"label": "1280x720", "w": 1280, "h": 720, "usable_h": 630}],
        "scenes": [
            {
                "id": "burn-ready",
                "area": "Burn window",
                "title": "Burn: ready",
                "about": "</script><b>an ending tag in the data</b>",
                "kind": "dialog",
                "variants": [],
                "texts": [{"kind": "label", "text": "Speed:", "tip": ""}],
                "skipped": [],
                "error": None,
                "min_size": [520, 480],
                "fixed": False,
            }
        ],
    }


def test_the_page_is_one_file_with_the_data_inside_and_cannot_be_broken_by_it(tmp_path):
    path = Path(gallery.write_gallery(manifest(), str(tmp_path)))
    html = path.read_text(encoding="utf-8")
    assert path.name == "index.html" and "<title>Dubcrate UI review</title>" in html
    blob = re.search(
        r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL
    ).group(1)
    data = json.loads(blob)
    assert data["scenes"][0]["id"] == "burn-ready"
    assert (
        "</script><b>" not in blob
    )  # (an ending tag inside the data would end the page's script)
    assert data["scenes"][0]["about"] == "</script><b>an ending tag in the data</b>"


def test_the_page_keeps_notes_under_a_key_of_its_own_per_round(tmp_path):
    html = Path(gallery.write_gallery(manifest(), str(tmp_path))).read_text(
        encoding="utf-8"
    )
    assert "dubcrate-ui-notes:v1" in html and "round-" in html  # (round 2 starts clean)
    assert "Export notes" in html and "search the text" in html


# -- the scenes ----------------------------------------------------------------------------------


def test_scene_ids_are_unique_and_each_has_a_known_area_and_a_title():
    ids = [s.id for s in scenes.SCENES]
    assert len(ids) == len(set(ids)) and len(ids) > 80
    for s in scenes.SCENES:
        assert s.area in scenes.AREAS and s.title.strip(), s.id
        assert s.kind in ("window", "dialog", "message", "menu"), s.id
        assert re.fullmatch(r"[a-z0-9-]+", s.id), s.id


def test_scenes_come_out_grouped_by_area_in_the_order_of_the_list():
    areas = [scenes.AREAS.index(s.area) for s in scenes.ordered()]
    assert areas == sorted(areas)


def test_every_area_has_scenes():
    assert {s.area for s in scenes.SCENES} == set(scenes.AREAS)


def ui_windows():
    """Every window and dialog class in the app, by name."""
    found = set()
    for path in (REPO / "src" / "dubcrate" / "ui").glob("*.py"):
        found |= set(
            re.findall(
                r"^class (\w+)\((?:QDialog|QMainWindow)\)",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
    return found | {"StartupSplash"}


@pytest.mark.parametrize("name", sorted(ui_windows()))
def test_every_window_and_dialog_of_the_app_has_a_scene(name):
    """The gallery is the checklist of the app's windows: a new window or dialog needs a scene."""
    source = (REPO / "scripts" / "ui_snapshot_scenes.py").read_text(encoding="utf-8")
    assert name in source, f"{name} has no scene in scripts/ui_snapshot_scenes.py"


def test_every_scene_that_is_not_a_plain_window_says_what_state_it_shows():
    assert [s.id for s in scenes.SCENES if not s.about.strip()] == []
