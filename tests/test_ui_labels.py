"""The Print Labels window and what it prints, without a printer."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPdfWriter, QPixmap

from dubcrate import labels, settings
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import label_dialog, label_paint
from dubcrate.ui.label_dialog import LabelDialog
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import SELECTION_ROLE


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        records = [
            TrackRecord(
                ext_id=str(i),
                path=f"/m/{i}.mp3",
                title=f"Song {i}",
                artist=f"Artist {i}",
                duration=200,
                bpm=120 + i,
                key="8A",
            )
            for i in range(12)
        ]
        library.replace_source("folder", "/m", "M", records)
        yield library


@pytest.fixture
def ids(lib):
    return [t.id for t in lib.tracks(sort="path")]


@pytest.fixture
def discs(lib, ids):
    one = lib.create_disc("One")
    lib.add_to_disc(one, ids[:6])
    two = lib.create_disc("Two")
    lib.add_to_disc(two, ids[6:12])
    empty = lib.create_disc("Empty")
    mp3 = lib.create_disc("Data", kind="mp3")
    lib.add_to_disc(mp3, ids[:2])
    return one, two, empty, mp3


def tick(dialog, *disc_ids):
    for i in range(dialog.disc_list.count()):
        item = dialog.disc_list.item(i)
        on = item.data(Qt.ItemDataRole.UserRole) in disc_ids
        item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)


def test_only_audio_discs_are_listed_and_the_picked_one_is_ticked(qapp, lib, discs):
    one = discs[0]
    dialog = LabelDialog(lib, picked=one)
    names = [dialog.disc_list.item(i).text() for i in range(dialog.disc_list.count())]
    assert names == ["One  (6)", "Two  (6)", "Empty  (0)"]  # no MP3 disc
    assert [d.id for d in dialog.ticked_discs()] == [one]


def test_an_empty_disc_is_not_ticked_for_you(qapp, lib, discs):
    dialog = LabelDialog(lib, picked=discs[2])
    assert dialog.ticked_discs() == []
    assert not dialog.print_button.isEnabled() and not dialog.pdf_button.isEnabled()


def test_discs_in_folders_are_named_with_their_path(qapp, lib, ids):
    folder = lib.create_disc_folder("Sets")
    disc = lib.create_disc("Opening")
    lib.move_disc(disc, folder)
    assert label_dialog.disc_paths(lib)[disc] == "Sets / Opening"


def test_several_discs_share_pages(qapp, lib, discs):
    one, two, *_ = discs
    dialog = LabelDialog(lib)
    tick(dialog, one, two)
    dialog.per_disc_spin.setValue(2)  # four labels at 10 x 5 cm: five fit on a page
    assert "4 labels on 1 page" in dialog.summary_label.text()
    assert dialog.next_button.isEnabled() is False
    dialog.copies_spin.setValue(2)  # burned twice: eight labels, two pages
    assert "8 labels on 2 pages" in dialog.summary_label.text()
    assert dialog.next_button.isEnabled()
    dialog.next_button.click()
    assert dialog.page_label.text() == "Page 2 of 2"


def test_places_already_used_push_labels_along(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.skip_spin.setValue(4)  # one place left on the first page
    dialog.per_disc_spin.setValue(2)
    assert "2 labels on 2 pages" in dialog.summary_label.text()


def test_a_label_too_small_for_its_tracks_says_so(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.height_spin.setValue(20)  # 6 tracks in 2 cm
    assert "too many tracks" in dialog.problems_label.text()
    assert not dialog.problems_label.isHidden()
    dialog.height_spin.setValue(50)
    assert dialog.problems_label.isHidden()


def test_a_label_wider_than_the_page_cannot_be_printed(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.width_spin.setValue(205)  # with the 10 mm margins, no room
    assert "does not fit" in dialog.problems_label.text()
    assert not dialog.print_button.isEnabled()


def test_the_preview_is_a_page_of_pixels(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.resize(900, 700)
    dialog.show()
    tick(dialog, discs[0])
    pixmap = dialog.preview.pixmap()
    assert isinstance(pixmap, QPixmap) and not pixmap.isNull()
    assert abs(pixmap.width() / pixmap.height() - 210 / 297) < 0.01
    dialog.close()


def test_the_choices_are_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.width_spin.setValue(90)
    dialog.per_disc_spin.setValue(3)
    dialog.bpm_key_check.setChecked(False)
    dialog.accept()
    saved = settings.get_label_options()
    assert (saved.width_mm, saved.per_disc, saved.show_bpm_key) == (90, 3, False)
    again = LabelDialog(lib)
    assert again.width_spin.value() == 90 and not again.bpm_key_check.isChecked()


def test_nonsense_in_the_settings_file_falls_back(qapp):
    qs = QSettings()
    qs.setValue("labels/width_mm", "wide")
    qs.setValue("labels/per_disc", 999)
    qs.setValue("labels/outline", "false")
    options = settings.get_label_options()
    assert options.width_mm == labels.LabelOptions().width_mm
    assert options.per_disc == labels.MAX_PER_DISC
    assert options.outline is False


def test_save_pdf_writes_one_pdf_page_per_sheet(
    qapp, lib, discs, tmp_path, monkeypatch
):
    one, two, *_ = discs
    dialog = LabelDialog(lib)
    tick(dialog, one, two)
    dialog.copies_spin.setValue(4)  # 8 labels: two pages
    target = tmp_path / "out"
    monkeypatch.setattr(label_dialog, "ask_pdf_path", lambda parent: str(target))
    dialog.save_pdf()
    data = (tmp_path / "out.pdf").read_bytes()  # ".pdf" added
    assert data.startswith(b"%PDF")
    assert data.count(b"/Type /Page\n") == 2 or data.count(b"/Type /Page") >= 2


def test_cancelling_the_file_chooser_writes_nothing(
    qapp, lib, discs, tmp_path, monkeypatch
):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    monkeypatch.setattr(label_dialog, "ask_pdf_path", lambda parent: "")
    dialog.save_pdf()
    assert list(tmp_path.iterdir()) == []


def test_cancelling_the_print_panel_prints_nothing(qapp, lib, discs, monkeypatch):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    asked = []
    monkeypatch.setattr(
        label_dialog,
        "ask_printer",
        lambda printer, parent: asked.append(printer) or False,
    )
    written = []
    monkeypatch.setattr(label_paint, "write_pages", lambda *a: written.append(a))
    dialog.print_labels()
    assert len(asked) == 1 and written == []


@pytest.mark.parametrize(
    "page,size",
    [
        ("A4", (210, 297)),
        ("A5", (148, 210)),
        ("Letter", (216, 279)),
        ("Legal", (216, 356)),
    ],
)
def test_the_page_the_printer_gets_is_the_chosen_paper_with_no_margins(
    qapp, page, size
):
    layout = label_paint.page_layout(labels.LabelOptions(page=page))
    mm = layout.pageSize().size(layout.pageSize().Unit.Millimeter)
    assert (round(mm.width()), round(mm.height())) == size
    assert layout.margins().left() == 0


def test_every_paper_size_has_a_qt_page_size(qapp):
    assert set(label_paint.PAGE_IDS) == set(labels.PAGE_SIZES)


def test_choosing_other_paper_changes_the_pages_and_the_preview(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.resize(900, 700)
    dialog.show()
    tick(dialog, discs[0], discs[1])
    dialog.per_disc_spin.setValue(2)  # four labels
    assert dialog.page_combo.currentData() == "A4"
    dialog.page_combo.setCurrentIndex(
        dialog.page_combo.findData("A5")
    )  # three to a page
    assert "4 labels on 2 pages" in dialog.summary_label.text()
    pixmap = dialog.preview.pixmap()
    assert abs(pixmap.width() / pixmap.height() - 148 / 210) < 0.01
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("Letter"))
    assert (
        abs(
            dialog.preview.pixmap().width() / dialog.preview.pixmap().height()
            - 215.9 / 279.4
        )
        < 0.01
    )
    dialog.close()


def test_a_label_cannot_be_made_bigger_than_the_paper(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("A6"))
    assert dialog.width_spin.maximum() == 105 and dialog.height_spin.maximum() == 148
    assert (
        dialog.width_spin.value() == 100
    )  # still fits as a label; the margins do not allow it
    tick(dialog, discs[0])
    assert "does not fit" in dialog.problems_label.text()


def test_the_paper_is_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("Letter"))
    dialog.accept()
    assert settings.get_label_options().page == "Letter"
    assert LabelDialog(lib).page_combo.currentData() == "Letter"
    QSettings().setValue("labels/page", "Foolscap")
    assert settings.get_label_options().page == "A4"


def test_the_pdf_is_the_chosen_paper(qapp, lib, discs, tmp_path, monkeypatch):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("Letter"))
    monkeypatch.setattr(
        label_dialog, "ask_pdf_path", lambda parent: str(tmp_path / "l.pdf")
    )
    dialog.save_pdf()
    data = (tmp_path / "l.pdf").read_bytes()
    assert b"/MediaBox [0 0 612.000000 792.000000]" in data  # US Letter in points


def test_the_painter_draws_onto_a_pdf(qapp, lib, discs, tmp_path):
    entries = lib.disc_entries(discs[0])
    options = labels.LabelOptions()
    made = labels.build_labels("One", entries, options)
    pages = labels.paginate(made, labels.sheet_for(options))
    writer = QPdfWriter(str(tmp_path / "x.pdf"))
    writer.setPageLayout(label_paint.page_layout(options))
    assert label_paint.write_pages(writer, pages, options)
    assert (tmp_path / "x.pdf").stat().st_size > 1000


# -- from the main window ------------------------------------------------------


def disc_index(win, disc_id):
    """The model index of a disc in the right-hand tree."""
    model = win.disc_tree.model()
    stack = [model.index(r, 0) for r in range(model.rowCount())]
    while stack:
        index = stack.pop()
        if index.data(SELECTION_ROLE) == ("disc", disc_id):
            return index
        stack += [model.index(r, 0, index) for r in range(model.rowCount(index))]
    raise AssertionError(f"disc {disc_id} is not in the tree")


def test_the_disc_menu_and_the_disc_list_open_the_window(qapp, lib, discs):
    one, two, _empty, mp3 = discs
    win = MainWindow(lib)
    try:
        win.open_disc(one)
        win.act_print_labels.trigger()
        assert [d.id for d in win.label_dialog.ticked_discs()] == [one]
        win.label_dialog.close()

        win.print_labels(two)
        assert [d.id for d in win.label_dialog.ticked_discs()] == [two]
        win.label_dialog.close()

        def menu_texts(disc_id):
            return [
                a.text() for a in win.disc_tree_menu(disc_index(win, disc_id)).actions()
            ]

        assert "Print Labels…" in menu_texts(one)
        assert "Print Labels…" not in menu_texts(mp3)  # an MP3 disc has no labels
    finally:
        win.close()
        win.deleteLater()


# -- font size -------------------------------------------------------------------


def test_the_font_starts_on_auto_and_the_summary_says_what_that_came_to(
    qapp, lib, discs
):
    dialog = LabelDialog(lib)
    assert dialog.font_spin.value() == 0 and dialog.font_spin.text() == "Auto"
    tick(dialog, discs[0])
    assert (
        "Text is 11.9 pt." in dialog.summary_label.text()
    )  # 4.2 mm, the most Auto gives
    dialog.font_spin.setValue(8)
    assert "Text is" not in dialog.summary_label.text()


def test_a_font_size_too_big_for_the_label_is_flagged(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.font_spin.setValue(20)  # six tracks at 20 pt in 5 cm
    assert "at this font size" in dialog.problems_label.text()
    assert "lower the font size" in dialog.problems_label.text()
    dialog.per_disc_spin.setValue(3)  # two tracks a label
    assert dialog.problems_label.isHidden()


def test_the_font_spin_skips_sizes_too_small_to_choose(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.font_spin.setValue(0.5)  # up from Auto
    assert dialog.font_spin.value() == labels.MIN_FONT_PT
    dialog.font_spin.setValue(3.5)  # down from the smallest
    assert dialog.font_spin.value() == 0


def test_the_font_size_is_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.font_spin.setValue(9.5)
    dialog.accept()
    assert settings.get_label_options().font_pt == 9.5
    assert LabelDialog(lib).font_spin.value() == 9.5
