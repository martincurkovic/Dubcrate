"""Index labels: splitting a track list, fitting text, and packing labels onto pages."""

from dataclasses import replace

import pytest

from dubcrate import labels
from dubcrate.labels import LabelOptions
from dubcrate.library.models import DiscEntry, Track


def entry(i, title=None, artist="Artist", bpm=None, key="", **kw):
    track = Track(
        id=i,
        source_id=1,
        ext_id=str(i),
        path=f"/m/{i}.mp3",
        title=title or f"Track {i + 1}",
        artist=artist,
        bpm=bpm,
        key=key,
    )
    return DiscEntry(position=i, track=track, **kw)


def entries(n, **kw):
    return [entry(i, **kw) for i in range(n)]


# -- spreading a disc over labels ---------------------------------------------


def test_split_evenly_keeps_order_and_gives_the_extra_to_the_first_runs():
    assert labels.split_evenly(list(range(7)), 3) == [[0, 1, 2], [3, 4], [5, 6]]
    assert labels.split_evenly(list(range(4)), 2) == [[0, 1], [2, 3]]


def test_split_evenly_never_makes_an_empty_run():
    assert labels.split_evenly([1, 2], 5) == [[1], [2]]
    assert labels.split_evenly([], 3) == [[]]


def test_a_disc_spread_over_two_labels_numbers_tracks_through_both():
    made = labels.build_labels("Night", entries(5), LabelOptions(per_disc=2))
    assert [l.heading for l in made] == ["Night  1/2", "Night  2/2"]
    assert [[r.number for r in l.rows] for l in made] == [[1, 2, 3], [4, 5]]


def test_one_label_has_the_plain_disc_name_and_an_empty_disc_has_none():
    [only] = labels.build_labels("Night", entries(3), LabelOptions())
    assert only.heading == "Night"
    assert labels.build_labels("Empty", [], LabelOptions()) == []


def test_every_label_of_a_disc_has_the_same_text_size():
    a, b = labels.build_labels("N", entries(9), LabelOptions(per_disc=2))
    assert a.font_mm == b.font_mm


# -- what is written -----------------------------------------------------------


def test_the_label_uses_the_discs_own_text_not_the_library_tracks():
    es = [entry(0, title="Library", artist="Lib", title_override="On the CD")]
    [label] = labels.build_labels("N", es, LabelOptions())
    assert label.rows[0].title == "On the CD"
    assert label.rows[0].artist == "Lib"


def test_bpm_is_rounded_and_the_key_follows_the_notation():
    es = [entry(0, bpm=127.6, key="Am"), entry(1)]
    [camelot] = labels.build_labels("N", es, LabelOptions(), "camelot")
    [musical] = labels.build_labels("N", es, LabelOptions(), "musical")
    assert (camelot.rows[0].bpm, camelot.rows[0].key) == ("128", "8A")
    assert musical.rows[0].key == "A min"
    assert (camelot.rows[1].bpm, camelot.rows[1].key) == ("", "")
    assert camelot.has_bpm and camelot.has_key


def test_bpm_and_key_can_be_left_off():
    es = [entry(0, bpm=120, key="8A")]
    [label] = labels.build_labels("N", es, LabelOptions(show_bpm_key=False))
    assert not label.has_bpm and not label.has_key


def test_no_bpm_is_not_zero():
    assert labels.bpm_text(None) == "" and labels.bpm_text(0) == ""
    assert labels.bpm_text(98.4) == "98"


# -- text size -----------------------------------------------------------------


def test_a_short_list_is_not_blown_up_and_a_long_one_shrinks():
    assert labels.font_size_mm(1, 50) == labels.MAX_FONT_MM
    assert labels.font_size_mm(20, 50) < labels.font_size_mm(10, 50)


def test_a_list_that_cannot_be_read_is_flagged_not_hidden():
    [label] = labels.build_labels("N", entries(30), LabelOptions(height_mm=30))
    assert not label.fits
    assert label.font_mm == labels.MIN_FONT_MM  # drawn at the smallest readable size
    [ok] = labels.build_labels("N", entries(10), LabelOptions())
    assert ok.fits


def test_ten_tracks_on_ten_by_five_centimetres_fit():
    assert labels.font_size_mm(10, 50) >= labels.MIN_FONT_MM


def test_the_longer_of_title_and_artist_gets_the_squeeze():
    assert labels.column_widths(30, 20, 100) == (30, 20)  # all fits: left whole
    assert labels.column_widths(20, 200, 100) == (20, 80)
    assert labels.column_widths(200, 20, 100) == (80, 20)
    assert labels.column_widths(200, 200, 100) == (50, 50)


# -- the sheet -----------------------------------------------------------------


def test_ten_by_five_centimetre_labels_fit_five_to_an_a4_page():
    sheet = labels.sheet_for(LabelOptions(width_mm=100, height_mm=50))
    assert (sheet.columns, sheet.rows, sheet.per_page) == (1, 5, 5)
    sheet = labels.sheet_for(
        LabelOptions(width_mm=100, height_mm=50, margin_mm=5, gap_mm=0)
    )
    assert (sheet.columns, sheet.rows) == (2, 5)


def test_a_label_bigger_than_the_page_has_no_sheet():
    assert (
        labels.sheet_for(LabelOptions(width_mm=210, height_mm=50, margin_mm=10)) is None
    )


def test_slots_run_along_the_row_then_down():
    sheet = labels.sheet_for(
        LabelOptions(width_mm=90, height_mm=50, margin_mm=10, gap_mm=2)
    )
    assert sheet.columns == 2
    assert sheet.position(0) == (10, 10)
    assert sheet.position(1) == (102, 10)
    assert sheet.position(2) == (10, 62)


def make_labels(n):
    return labels.build_labels("N", entries(n), LabelOptions(per_disc=n)) if n else []


def test_labels_from_several_discs_share_pages():
    options = LabelOptions(width_mm=100, height_mm=50)
    sheet = labels.sheet_for(options)
    discs = [
        label
        for name in "ABC"
        for label in labels.build_labels(name, entries(4), LabelOptions(per_disc=2))
    ]
    pages = labels.paginate(discs, sheet)
    assert [len(p) for p in pages] == [5, 1]  # six labels, five to a page
    assert [p.label.heading for p in pages[0]] == [
        "A  1/2", "A  2/2", "B  1/2", "B  2/2", "C  1/2",
    ]  # fmt: skip


def test_skipping_places_leaves_the_first_ones_of_the_first_page_empty():
    sheet = labels.sheet_for(LabelOptions(width_mm=100, height_mm=50))
    pages = labels.paginate(make_labels(1) * 3, sheet, skip=3)
    assert [len(p) for p in pages] == [2, 1]
    assert pages[0][0].y_mm == sheet.position(3)[1]


def test_skipping_more_than_a_page_is_held_to_the_last_place():
    sheet = labels.sheet_for(LabelOptions(width_mm=100, height_mm=50))
    [page] = labels.paginate(make_labels(1), sheet, skip=99)
    assert page[0].y_mm == sheet.position(sheet.per_page - 1)[1]


def test_no_labels_make_no_pages():
    sheet = labels.sheet_for(LabelOptions())
    assert labels.paginate([], sheet) == []


# -- the options ----------------------------------------------------------------


def test_options_are_held_inside_their_limits():
    o = LabelOptions(
        width_mm=1, height_mm=9999, per_disc=0, copies=99, margin_mm=-3, gap_mm=500
    ).clamped()
    assert o.width_mm == labels.MIN_LABEL_MM
    assert o.height_mm == labels.PAGE_SIZES["A4"][1]
    assert (o.per_disc, o.copies) == (1, labels.MAX_COPIES)
    assert (o.margin_mm, o.gap_mm) == (0, 20)


@pytest.mark.parametrize("w,h", [(100, 50), (60, 40), (190, 277)])
def test_a_sheet_never_places_a_label_off_the_page(w, h):
    options = LabelOptions(width_mm=w, height_mm=h)
    sheet = labels.sheet_for(options)
    x, y = sheet.position(sheet.per_page - 1)
    assert x + w <= 210 and y + h <= 297


# -- other paper -----------------------------------------------------------------


def test_the_paper_sizes_are_portrait_and_a4_is_the_default():
    assert labels.LabelOptions().page == "A4"
    assert all(w < h for w, h in labels.PAGE_SIZES.values())
    assert {"A4", "A5", "Letter", "Legal"} <= set(labels.PAGE_SIZES)


def test_smaller_paper_fits_fewer_labels_and_us_letter_is_a_different_shape():
    base = LabelOptions(width_mm=100, height_mm=50)
    a4 = labels.sheet_for(base)
    a5 = labels.sheet_for(replace(base, page="A5"))
    letter = labels.sheet_for(replace(base, page="Letter"))
    assert a4.per_page == 5
    assert (a5.columns, a5.rows) == (1, 3)  # 148 x 210 mm
    assert (letter.columns, letter.rows) == (1, 5)  # 215.9 x 279.4 mm: wider, shorter
    legal = labels.sheet_for(replace(base, page="Legal"))
    assert legal.rows == 6  # 355.6 mm


def test_a_label_that_fits_a4_may_not_fit_a_smaller_page():
    options = LabelOptions(
        width_mm=100, height_mm=50, page="A6"
    )  # 105 mm wide, 10 mm margins
    assert labels.sheet_for(options) is None


def test_a_label_is_held_to_the_paper_and_unknown_paper_is_a4():
    o = LabelOptions(width_mm=200, height_mm=200, page="A5").clamped()
    assert (o.width_mm, o.height_mm) == (148, 200)
    assert LabelOptions(page="Foolscap").clamped().page == "A4"


def test_labels_are_placed_by_the_papers_size_not_a4s():
    options = LabelOptions(width_mm=60, height_mm=40, page="A5")
    sheet = labels.sheet_for(options)
    x, y = sheet.position(sheet.per_page - 1)
    assert x + 60 <= 148 and y + 40 <= 210


# -- font size ------------------------------------------------------------------


def test_a_chosen_font_size_is_used_for_every_label_as_it_is():
    made = labels.build_labels("N", entries(6), LabelOptions(font_pt=10, per_disc=2))
    assert all(abs(l.font_mm - 10 * labels.MM_PER_PT) < 1e-9 for l in made)
    assert all(l.fits for l in made)


def test_a_chosen_size_can_be_bigger_than_auto_would_pick():
    [auto] = labels.build_labels("N", entries(2), LabelOptions())
    [big] = labels.build_labels("N", entries(2), LabelOptions(font_pt=24))
    assert auto.font_mm == labels.MAX_FONT_MM
    assert big.font_mm > auto.font_mm


def test_a_chosen_size_that_does_not_fit_is_flagged_and_not_shrunk():
    [label] = labels.build_labels("N", entries(10), LabelOptions(font_pt=14))
    assert not label.fits
    assert (
        abs(label.font_mm - 14 * labels.MM_PER_PT) < 1e-9
    )  # drawn as asked; the dialog warns
    [ok] = labels.build_labels("N", entries(10), LabelOptions(font_pt=8))
    assert ok.fits


def test_spreading_a_disc_over_more_labels_makes_a_big_font_fit():
    options = LabelOptions(font_pt=14)
    assert not labels.build_labels("N", entries(10), options)[0].fits
    assert all(
        l.fits
        for l in labels.build_labels("N", entries(10), replace(options, per_disc=2))
    )


def test_auto_is_zero_and_a_chosen_size_is_held_inside_its_limits():
    assert LabelOptions().font_pt == 0
    assert LabelOptions(font_pt=-3).clamped().font_pt == 0
    assert LabelOptions(font_pt=1).clamped().font_pt == labels.MIN_FONT_PT
    assert LabelOptions(font_pt=99).clamped().font_pt == labels.MAX_FONT_PT
    assert LabelOptions(font_pt=9.5).clamped().font_pt == 9.5
