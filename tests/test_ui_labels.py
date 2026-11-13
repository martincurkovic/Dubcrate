"""The Print Labels window and what it prints, without a printer."""

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPdfWriter
from PySide6.QtWidgets import QApplication

from dubcrate import labels, settings
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import label_dialog, label_paint
from dubcrate.ui.label_dialog import LabelDialog
from dubcrate.ui.label_preview import MIN_ZOOM, ZOOM_STEPS
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
    for item in dialog._disc_items():
        on = item.data(0, Qt.ItemDataRole.UserRole) in disc_ids
        item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)


def test_every_disc_is_listed_and_the_picked_one_is_ticked(qapp, lib, discs):
    one = discs[0]
    dialog = LabelDialog(lib, picked=one)
    names = [i.text(0) for i in dialog._disc_items()]
    assert names == ["Data  (2)  MP3", "Empty  (0)", "One  (6)", "Two  (6)"]  # A to Z
    assert [d.id for d in dialog.ticked_discs()] == [one]


def test_an_empty_disc_is_not_ticked_for_you(qapp, lib, discs):
    dialog = LabelDialog(lib, picked=discs[2])
    assert dialog.ticked_discs() == []
    assert not dialog.print_button.isEnabled() and not dialog.pdf_button.isEnabled()


def tree_shape(dialog):
    """The picker as nested (text, children) pairs."""

    def shape(item):
        return (item.text(0), [shape(item.child(r)) for r in range(item.childCount())])

    tree = dialog.disc_tree
    return [shape(tree.topLevelItem(r)) for r in range(tree.topLevelItemCount())]


@pytest.fixture
def filed(lib, ids):
    """Sets / (Friday, Saturday / Late), Spare, and discs at the top: Zed and Alpha."""
    sets = lib.create_disc_folder("Sets")
    late = lib.create_disc_folder("Late", parent_id=sets)
    lib.create_disc_folder("Nothing in here")
    made = {}
    for name, folder in (
        ("Friday", sets),
        ("Saturday", late),
        ("Sunday", late),
        ("Zed", None),
        ("Alpha", None),
    ):
        disc = lib.create_disc(name)
        lib.add_to_disc(disc, ids[:3])
        if folder is not None:
            lib.move_disc(disc, folder)
        made[name] = disc
    blank = lib.create_disc("Blank")  # no tracks
    lib.move_disc(blank, late)
    made["Blank"] = blank
    return made


def test_the_discs_are_shown_in_the_folders_that_hold_them(qapp, lib, filed):
    dialog = LabelDialog(lib)
    assert tree_shape(dialog) == [
        (
            "Sets",
            [
                (
                    "Late",
                    [("Blank  (0)", []), ("Saturday  (3)", []), ("Sunday  (3)", [])],
                ),
                ("Friday  (3)", []),
            ],
        ),  # folders first, then discs, each A to Z, as in the Discs list
        ("Alpha  (3)", []),
        ("Zed  (3)", []),
    ]  # (and no "Nothing in here": a folder with no disc is left out)
    assert dialog.disc_tree.topLevelItem(
        0
    ).isExpanded()  # opened, so every disc is in view


def test_a_folder_that_holds_only_discs_with_no_tracks_has_no_tick_box(qapp, lib):
    folder = lib.create_disc_folder("Ideas")
    disc = lib.create_disc("Empty")
    lib.move_disc(disc, folder)
    dialog = LabelDialog(lib)
    item = dialog.disc_tree.topLevelItem(0)
    assert item.text(0) == "Ideas" and item.child(0).text(0) == "Empty  (0)"
    assert item.checkState(0) == Qt.CheckState.Unchecked  # (no box is drawn)
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert not item.child(0).flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_ticking_a_folder_ticks_every_disc_in_it_and_leaves_empty_ones(
    qapp, lib, filed
):
    dialog = LabelDialog(lib)
    sets = dialog.disc_tree.topLevelItem(0)
    sets.setCheckState(0, Qt.CheckState.Checked)
    assert [d.name for d in dialog.ticked_discs()] == ["Saturday", "Sunday", "Friday"]
    late = sets.child(0)
    assert late.checkState(0) == Qt.CheckState.Checked  # the folder inside it too
    assert (
        late.child(0).checkState(0) == Qt.CheckState.Unchecked
    )  # Blank: nothing to print
    sets.setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog.ticked_discs() == []


def test_a_folder_shows_when_only_some_of_its_discs_are_ticked(qapp, lib, filed):
    dialog = LabelDialog(lib)
    sets = dialog.disc_tree.topLevelItem(0)
    late = sets.child(0)
    tick(dialog, filed["Saturday"])
    assert late.checkState(0) == Qt.CheckState.PartiallyChecked
    assert sets.checkState(0) == Qt.CheckState.PartiallyChecked
    tick(dialog, filed["Saturday"], filed["Sunday"])
    assert late.checkState(0) == Qt.CheckState.Checked
    assert sets.checkState(0) == Qt.CheckState.PartiallyChecked  # Friday is still out
    tick(dialog, filed["Saturday"], filed["Sunday"], filed["Friday"])
    assert sets.checkState(0) == Qt.CheckState.Checked
    tick(dialog)
    assert sets.checkState(0) == Qt.CheckState.Unchecked


def test_ticking_a_folder_plans_the_labels_once(qapp, lib, filed, monkeypatch):
    dialog = LabelDialog(lib)
    planned = []
    real = dialog.refresh
    monkeypatch.setattr(dialog, "refresh", lambda *a: (planned.append(1), real(*a))[1])
    dialog.disc_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    assert len(planned) == 1  # not once for each disc the tick reached


def test_all_and_none_tick_every_disc_that_has_tracks(qapp, lib, filed):
    dialog = LabelDialog(lib)
    dialog._tick_all(Qt.CheckState.Checked)
    assert sorted(d.name for d in dialog.ticked_discs()) == [
        "Alpha", "Friday", "Saturday", "Sunday", "Zed",
    ]  # fmt: skip
    assert dialog.disc_tree.topLevelItem(0).checkState(0) == Qt.CheckState.Checked
    dialog._tick_all(Qt.CheckState.Unchecked)
    assert dialog.ticked_discs() == []
    assert dialog.disc_tree.topLevelItem(0).checkState(0) == Qt.CheckState.Unchecked


def test_the_label_is_still_titled_with_the_folders_the_disc_is_in(qapp, lib, filed):
    dialog = LabelDialog(lib, picked=filed["Saturday"])
    assert dialog.ticked_discs()[0].name == "Saturday"
    assert label_dialog.disc_paths(lib)[filed["Saturday"]] == "Sets / Late / Saturday"
    headings = [placed.label.heading for page in dialog._pages for placed in page]
    assert headings == [
        "Sets / Late / Saturday"
    ]  # (printed as before: the sticker has no tree)


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


def test_a_label_too_small_for_its_tracks_says_so(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.per_disc_spin.setValue(1)
    dialog.height_spin.setValue(20)  # 6 tracks in 2 cm
    assert "too many tracks" in dialog.problems_label.text()
    dialog.height_spin.setValue(50)
    assert dialog.problems_label.text() == ""


def test_a_label_wider_than_the_page_cannot_be_printed(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])
    dialog.width_spin.setValue(205)  # with the 10 mm margins, no room
    assert "does not fit" in dialog.problems_label.text()
    assert not dialog.print_button.isEnabled()


def aspect(preview):
    size = preview.content_size()
    return size.width() / size.height()


def test_the_preview_is_a_whole_page_that_fits_the_pane(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.resize(900, 700)
    dialog.show()
    tick(dialog, discs[0])
    assert abs(aspect(dialog.preview) - 210 / 297) < 0.01
    view, page = dialog.preview.viewport().size(), dialog.preview.content_size()
    assert page.width() <= view.width() and page.height() <= view.height()
    assert dialog.preview.horizontalScrollBar().maximum() == 0  # all of it shows
    dialog.close()


def test_the_choices_are_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.width_spin.setValue(90)
    dialog.per_disc_spin.setValue(3)
    dialog.bpm_check.setChecked(False)
    dialog.accept()
    saved = settings.get_label_options()
    assert (saved.width_mm, saved.per_disc) == (90, 3)
    assert (saved.show_bpm, saved.show_key) == (False, True)
    again = LabelDialog(lib)
    assert again.width_spin.value() == 90 and not again.bpm_check.isChecked()


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
    assert abs(aspect(dialog.preview) - 148 / 210) < 0.01
    dialog.page_combo.setCurrentIndex(dialog.page_combo.findData("Letter"))
    assert abs(aspect(dialog.preview) - 215.9 / 279.4) < 0.01
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
        assert "Print Labels…" in menu_texts(mp3)  # an MP3 disc has labels too
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
    dialog.per_disc_spin.setValue(1)
    dialog.font_spin.setValue(20)  # six tracks at 20 pt in 5 cm
    assert "at this font size" in dialog.problems_label.text()
    assert "lower the font size" in dialog.problems_label.text()
    dialog.per_disc_spin.setValue(3)  # two tracks a label
    assert dialog.problems_label.text() == ""


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


# -- MP3 discs, Auto and columns ------------------------------------------------------


@pytest.fixture
def mp3_disc(lib):
    """An MP3 disc: 6 tracks on the root, 20 in "Peak", 20 in "Warm up"."""
    records = [
        TrackRecord(
            ext_id=f"x{i}",
            path=f"/x/{i}.mp3",
            title=f"Tune {i}",
            artist="Someone",
            duration=100,
            bpm=125,
            key="8A",
        )
        for i in range(46)
    ]
    lib.replace_source("folder", "/x", "X", records)
    ids = [t.id for t in lib.tracks(sort="path") if t.path.startswith("/x/")]
    disc = lib.create_disc("Trax", kind="mp3")
    peak = lib.create_disc_dir(disc, "Peak")
    warm = lib.create_disc_dir(disc, "Warm up")
    lib.add_to_disc(disc, ids[:6])
    lib.add_to_disc(disc, ids[6:26], dir_id=peak)
    lib.add_to_disc(disc, ids[26:46], dir_id=warm)
    return disc


def folder_names(dialog):
    return [
        item.name
        for page in dialog._pages
        for placed in page
        for column in placed.label.columns
        for item in column
        if isinstance(item, labels.FolderRow)
    ]


def test_an_mp3_disc_prints_with_its_folders_as_headings(qapp, lib, mp3_disc):
    dialog = LabelDialog(lib, picked=mp3_disc)
    assert [d.id for d in dialog.ticked_discs()] == [mp3_disc]
    assert dialog.print_button.isEnabled()
    names = folder_names(dialog)
    assert "Peak" in names and "Warm up" in names
    assert names.index("Peak") < names.index("Warm up")


def test_auto_is_the_default_number_of_labels_and_it_grows_with_the_disc(
    qapp, lib, discs, mp3_disc
):
    dialog = LabelDialog(lib)
    assert dialog.per_disc_spin.value() == 0 and dialog.per_disc_spin.text() == "Auto"
    tick(dialog, discs[0])
    assert "1 label on 1 page" in dialog.summary_label.text()  # six tracks
    tick(dialog, mp3_disc)
    assert "labels on 1 page" in dialog.summary_label.text()
    assert "1 label on" not in dialog.summary_label.text()  # 48 lines need several


def test_columns_let_a_long_disc_use_fewer_labels(qapp, lib, mp3_disc):
    dialog = LabelDialog(lib)
    tick(dialog, mp3_disc)

    def label_count():
        return sum(len(page) for page in dialog._pages)

    dialog.columns_combo.setCurrentIndex(0)
    one_column = label_count()
    dialog.columns_combo.setCurrentIndex(1)
    assert label_count() < one_column
    assert any(len(p.label.columns) == 2 for page in dialog._pages for p in page)


def test_too_many_tracks_suggests_what_to_change(qapp, lib, mp3_disc):
    dialog = LabelDialog(lib)
    tick(dialog, mp3_disc)
    dialog.per_disc_spin.setValue(1)
    dialog.height_spin.setValue(30)
    text = dialog.problems_label.text()
    assert "too many tracks" in text and "allow more columns" in text
    assert "spread the disc over more labels" in text
    dialog.columns_combo.setCurrentIndex(2)
    assert "allow more columns" not in dialog.problems_label.text()


def test_columns_and_auto_are_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.columns_combo.setCurrentIndex(1)
    dialog.per_disc_spin.setValue(0)
    dialog.accept()
    saved = settings.get_label_options()
    assert (saved.columns, saved.per_disc) == (2, 0)
    QSettings().setValue("labels/per_disc", 1)  # a choice made before Auto existed
    assert LabelDialog(lib).per_disc_spin.value() == 1


def test_a_multi_column_page_prints_to_a_pdf(
    qapp, lib, mp3_disc, tmp_path, monkeypatch
):
    dialog = LabelDialog(lib)
    tick(dialog, mp3_disc)
    dialog.columns_combo.setCurrentIndex(2)
    monkeypatch.setattr(
        label_dialog, "ask_pdf_path", lambda parent: str(tmp_path / "m.pdf")
    )
    dialog.save_pdf()
    assert (tmp_path / "m.pdf").read_bytes().startswith(b"%PDF")


# -- the other controls ------------------------------------------------------------------


def test_columns_is_a_box_of_one_to_three_and_places_used_is_gone(qapp, lib, discs):
    dialog = LabelDialog(lib)
    combo = dialog.columns_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 2, 3]
    assert combo.currentData() == 1
    assert not hasattr(dialog, "skip_spin")
    combo.setCurrentIndex(combo.findData(3))
    assert dialog.options().columns == 3


def test_a_stored_number_of_columns_is_shown(qapp, lib, discs):
    QSettings().setValue("labels/columns", 2)
    assert LabelDialog(lib).columns_combo.currentData() == 2


def test_the_messages_have_room_set_aside_so_nothing_moves_when_they_come(
    qapp, lib, discs
):
    dialog = LabelDialog(lib)
    dialog.resize(1000, 760)
    dialog.show()
    tick(dialog, discs[0])
    dialog.per_disc_spin.setValue(1)
    dialog.height_spin.setValue(50)
    qapp.processEvents()
    assert dialog.problems_label.text() == ""
    controls = (
        dialog.width_spin,
        dialog.copies_spin,
        dialog.bpm_check,
        dialog.key_check,
        dialog.duration_check,
        dialog.outline_check,
        dialog.summary_label,
        dialog.problems_label,
        dialog.disc_tree,
    )
    before = [w.geometry() for w in controls]
    dialog.height_spin.setValue(20)  # a warning appears (see the test above)
    qapp.processEvents()
    assert dialog.problems_label.text() != ""
    assert [w.geometry() for w in controls] == before
    dialog.height_spin.setValue(50)
    qapp.processEvents()
    assert [w.geometry() for w in controls] == before
    dialog.close()


def test_the_messages_never_outgrow_their_space(qapp, lib, discs):
    dialog = LabelDialog(lib)
    dialog.show()
    tick(dialog, discs[0], discs[1])
    dialog.per_disc_spin.setValue(1)
    dialog.height_spin.setValue(20)  # both discs too long
    text = dialog.problems_label.text()
    assert text.count("too many tracks") == 1 and "and 1 more" in text
    dialog.close()


# -- the preview's zoom ---------------------------------------------------------------------


@pytest.fixture
def shown(qapp, lib, discs):
    dialog = LabelDialog(lib, picked=discs[0])
    dialog.resize(1000, 760)
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()


def test_the_zoom_box_offers_fit_and_real_sizes(shown):
    combo = shown.zoom_combo
    assert combo.itemText(0) == "Fit page" and combo.currentData() == 0.0
    assert [combo.itemText(i) for i in range(1, combo.count())] == [
        "100%",
        "150%",
        "200%",
        "300%",
        "400%",
    ]


def test_zooming_in_makes_the_page_bigger_and_scrollable(shown):
    preview = shown.preview
    fitted = preview.content_size()
    shown.zoom_combo.setCurrentIndex(shown.zoom_combo.findData(2.0))
    zoomed = preview.content_size()
    assert zoomed.width() > fitted.width() and zoomed.height() > fitted.height()
    assert preview.zoom() == 2.0
    assert preview.horizontalScrollBar().maximum() > 0
    assert preview.verticalScrollBar().maximum() > 0
    shown.zoom_combo.setCurrentIndex(0)
    assert preview.horizontalScrollBar().maximum() == 0


def test_a_hundred_percent_is_the_page_at_about_real_size(shown):
    shown.zoom_combo.setCurrentIndex(shown.zoom_combo.findData(1.0))
    ratio = shown.preview.devicePixelRatioF()
    size = shown.preview.content_size()
    # A4 is 8.27 x 11.69 inches; at 96 dpi that is 794 x 1123 logical pixels
    assert abs(size.width() - 794) <= 2 and abs(size.height() - 1123) <= 2
    assert ratio >= 1


def test_the_preview_draws_at_the_screens_real_pixels_not_a_scaled_picture(shown):
    preview = shown.preview
    assert preview._device_dpi() == round(
        96 * preview.zoom() * preview.devicePixelRatioF()
    )


def test_drawing_part_of_a_page_matches_the_same_part_of_the_whole(qapp, lib, discs):
    from PySide6.QtCore import QRect

    from dubcrate.ui import label_paint

    options = labels.LabelOptions()
    made = labels.build_labels("One", lib.disc_entries(discs[0]), options)
    pages = labels.paginate(made, labels.sheet_for(options))
    whole = label_paint.page_image(pages[0], options, 200)
    region = QRect(120, 150, 400, 300)
    part = label_paint.page_image(pages[0], options, 200, region)
    assert part.size() == region.size()
    assert part == whole.copy(region)  # same pixels: zoomed drawing is the same drawing


def test_ctrl_wheel_zooms_and_a_plain_wheel_scrolls(shown):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    def wheel(delta, modifiers):
        event = QWheelEvent(
            QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False,
        )  # fmt: skip
        shown.preview.wheelEvent(event)

    ctrl = Qt.KeyboardModifier.ControlModifier
    wheel(120, ctrl)
    assert shown.zoom_combo.currentData() == 1.0  # up from Fit: the first step
    wheel(120, ctrl)
    assert shown.zoom_combo.currentData() == 1.5
    wheel(-120, ctrl)
    wheel(-120, ctrl)
    assert shown.zoom_combo.currentData() == 0.0  # back to Fit
    shown.zoom_combo.setCurrentIndex(shown.zoom_combo.findData(2.0))
    bar = shown.preview.verticalScrollBar()
    wheel(-120, Qt.KeyboardModifier.NoModifier)
    assert bar.value() > 0 and shown.zoom_combo.currentData() == 2.0


def test_dragging_a_zoomed_page_pans_it(shown):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    shown.zoom_combo.setCurrentIndex(shown.zoom_combo.findData(2.0))
    preview, bar = shown.preview, shown.preview.verticalScrollBar()
    bar.setValue(300)

    def mouse(kind, y, buttons):
        QApplication.sendEvent(
            preview.viewport(),
            QMouseEvent(kind, QPointF(50, y), QPointF(50, y), Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier),
        )  # fmt: skip

    left = Qt.MouseButton.LeftButton
    mouse(QEvent.Type.MouseButtonPress, 200, left)
    mouse(QEvent.Type.MouseMove, 150, left)  # the hand moves up, the page follows it
    assert bar.value() == 350
    mouse(QEvent.Type.MouseButtonRelease, 150, Qt.MouseButton.NoButton)
    mouse(QEvent.Type.MouseMove, 100, Qt.MouseButton.NoButton)
    assert bar.value() == 350  # no longer dragging


def pinch(preview, value, at=(200, 200)):
    """A trackpad pinch: Qt hands the viewport a native zoom gesture whose value is the change."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice

    point = QPointF(*at)
    event = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        2,
        point,
        point,
        point,
        value,
        QPointF(0, 0),
    )
    QApplication.sendEvent(preview.viewport(), event)


def test_a_pinch_zooms_smoothly_and_the_zoom_box_follows(shown):
    preview, box = shown.preview, shown.zoom_combo
    fitted = preview.zoom()
    pinch(preview, 0.5)
    assert abs(preview.zoom() - fitted * 1.5) < 1e-9  # not one of the fixed steps
    assert box.currentText() == f"{fitted * 1.5 * 100:.0f}%"  # a line of its own
    assert box.count() == len(ZOOM_STEPS) + 2
    pinch(preview, 0.1)
    assert box.count() == len(ZOOM_STEPS) + 2  # the same line, updated
    assert box.currentData() == preview.zoom()


def test_a_pinch_stops_at_the_ends_of_the_zoom_range(shown):
    preview = shown.preview
    for _ in range(40):
        pinch(preview, 0.5)
    assert preview.zoom() == ZOOM_STEPS[-1]
    for _ in range(80):
        pinch(preview, -0.5)
    assert preview.zoom() == MIN_ZOOM


def test_a_pinch_zooms_about_the_fingers_and_not_the_pages_centre(shown):
    preview = shown.preview
    shown.zoom_combo.setCurrentIndex(shown.zoom_combo.findData(2.0))
    preview.horizontalScrollBar().setValue(100)
    preview.verticalScrollBar().setValue(200)
    at = (150, 250)
    before = preview.content_size()
    fx = (150 + 100) / before.width()  # the page position under the fingers
    fy = (250 + 200) / before.height()
    pinch(preview, 0.5, at)
    after = preview.content_size()
    assert after.width() > before.width()
    assert (
        abs((150 + preview.horizontalScrollBar().value()) / after.width() - fx) < 0.01
    )
    assert abs((250 + preview.verticalScrollBar().value()) / after.height() - fy) < 0.01


def test_choosing_from_the_zoom_box_after_a_pinch_drops_the_odd_line(shown):
    box = shown.zoom_combo
    pinch(shown.preview, 0.3)
    assert box.count() == len(ZOOM_STEPS) + 2
    box.setCurrentIndex(box.findData(1.5))
    assert box.count() == len(ZOOM_STEPS) + 1
    assert shown.preview.zoom() == 1.5
    pinch(shown.preview, 0.0)  # a pinch that lands on a step shows that step
    assert box.currentData() == 1.5 and box.count() == len(ZOOM_STEPS) + 1


def test_other_native_gestures_are_left_alone(shown):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QNativeGestureEvent, QPointingDevice

    point = QPointF(100, 100)
    event = QNativeGestureEvent(
        Qt.NativeGestureType.RotateNativeGesture,
        QPointingDevice.primaryPointingDevice(),
        2, point, point, point, 15.0, QPointF(0, 0),
    )  # fmt: skip
    before = shown.preview.zoom()
    QApplication.sendEvent(shown.preview.viewport(), event)
    assert shown.preview.zoom() == before


def test_bpm_and_key_are_separate_boxes_that_each_change_the_labels(qapp, lib, discs):
    dialog = LabelDialog(lib)
    tick(dialog, discs[0])

    def shown():
        rows = [r for page in dialog._pages for p in page for r in p.label.rows]
        return any(r.bpm for r in rows), any(r.key for r in rows)

    assert shown() == (True, True)
    dialog.bpm_check.setChecked(False)
    assert shown() == (False, True)
    dialog.key_check.setChecked(False)
    assert shown() == (False, False)
    dialog.bpm_check.setChecked(True)
    assert shown() == (True, False)


def test_a_choice_saved_with_the_old_single_box_applies_to_both_new_ones(qapp, lib):
    QSettings().setValue("labels/show_bpm_key", False)
    options = settings.get_label_options()
    assert (options.show_bpm, options.show_key) == (False, False)
    dialog = LabelDialog(lib)
    assert not dialog.bpm_check.isChecked() and not dialog.key_check.isChecked()
    dialog.key_check.setChecked(True)
    dialog.accept()  # saving writes the two new keys and drops the old one
    assert not QSettings().contains("labels/show_bpm_key")
    assert (
        settings.get_label_options().show_bpm,
        settings.get_label_options().show_key,
    ) == (False, True)


def test_duration_is_its_own_box_off_to_begin_with_and_remembered(qapp, lib, discs):
    dialog = LabelDialog(lib)
    assert not dialog.duration_check.isChecked() and dialog.bpm_check.isChecked()
    tick(dialog, discs[0])

    def lengths():
        return {r.length for page in dialog._pages for p in page for r in p.label.rows}

    assert lengths() == {""}
    dialog.duration_check.setChecked(True)
    assert lengths() == {"3:20"}  # the fixture's tracks are 200 s long
    dialog.accept()
    assert settings.get_label_options().show_duration is True
    assert LabelDialog(lib).duration_check.isChecked()


def test_all_three_side_columns_print_in_one_and_in_several_columns(
    qapp, lib, mp3_disc, tmp_path, monkeypatch
):
    dialog = LabelDialog(lib)
    tick(dialog, mp3_disc)
    dialog.duration_check.setChecked(True)
    for columns in (1, 3):
        dialog.columns_combo.setCurrentIndex(dialog.columns_combo.findData(columns))
        target = tmp_path / f"c{columns}.pdf"
        monkeypatch.setattr(
            label_dialog, "ask_pdf_path", lambda parent, t=target: str(t)
        )
        dialog.save_pdf()
        assert target.read_bytes().startswith(b"%PDF")
