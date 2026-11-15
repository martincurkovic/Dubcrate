"""The CDJ waveform drawn in the Sync tab: the strip widget, and which waveform each track shows."""

import pytest
from PySide6.QtGui import QColor

from dubcrate import card_discs, wave_model
from dubcrate import cdj_card as cc
from dubcrate.library import Library, TrackRecord
from dubcrate.ui.wave_strip import (
    CARD,
    DASH,
    LIT,
    NONE,
    PADDING,
    PANEL,
    PENDING,
    ROW_PITCH,
    ROWS,
    WRITE,
    CdjWaveStrip,
    rows_for,
)


def saw(shift=0):
    return [1 + (k + shift) % 15 for k in range(100)]


# -- the widget -------------------------------------------------------------------------------------


def test_fifteen_lights_all_seven_rows_zero_none_and_anything_audible_at_least_one():
    assert [rows_for(v) for v in (0, 1, 2, 3, 8, 14, 15)] == [0, 1, 1, 1, 4, 7, 7]
    assert (
        rows_for(-3) == 0 and rows_for(99) == 7
    )  # (out of range is held to the range)
    assert ROWS == 7
    strip = CdjWaveStrip([15, 0, 5, 99, -3] + [1] * 95)
    assert strip.lit_rows()[:5] == [7, 0, 2, 7, 0]


def sized(strip, width=560):
    strip.resize(width, strip.heightForWidth(width))
    return strip


def test_the_display_is_a_hundred_columns_and_the_bars_grow_from_the_bottom(qapp):
    strip = sized(CdjWaveStrip([15] + [0] * 99, WRITE))
    image = strip.grab().toImage()
    white = LIT[WRITE].name()
    lit = [
        (x, y)
        for x in range(image.width())
        for y in range(image.height())
        if QColor(image.pixel(x, y)).name() == white
    ]
    assert lit  # (the first column, full height)
    assert (
        max(x for x, _ in lit) < PADDING + (560 - 2 * PADDING) / 100 + 1
    )  # only the first column is lit
    height = image.height()
    assert max(y for _, y in lit) > height - PADDING - 3 and min(y for _, y in lit) < PADDING + 3  # fmt: skip
    short = sized(CdjWaveStrip([2] + [0] * 99, WRITE))
    image = short.grab().toImage()
    ys = [
        y
        for x in range(image.width())
        for y in range(image.height())
        if QColor(image.pixel(x, y)).name() == white
    ]
    assert min(ys) > image.height() / 2  # a quiet column lights only the bottom rows


def test_the_matrix_keeps_the_players_proportions_at_any_width(qapp):
    strip = CdjWaveStrip(saw())
    for width in (300, 560, 900):
        pitch = (width - 2 * PADDING) / 100
        assert strip.heightForWidth(width) == round(pitch * ROW_PITCH * ROWS) + 2 * PADDING  # fmt: skip
    # a thin strip, as on the player (about twelve times as wide as it is tall), not a block
    inner_w, inner_h = 560 - 2 * PADDING, strip.heightForWidth(560) - 2 * PADDING
    assert 11 < inner_w / inner_h < 14
    assert (
        strip.hasHeightForWidth() and strip.minimumWidth() == 0
    )  # (its width is never the pane's)


def colours(strip):
    image = strip.grab().toImage()
    return {
        QColor(image.pixel(x, y)).name()
        for x in range(image.width())
        for y in range(image.height())
    }


def test_a_waveform_to_write_is_white_and_one_on_the_card_dimmer(qapp):
    write = sized(CdjWaveStrip(saw(), WRITE))
    card = sized(CdjWaveStrip(saw(), CARD))
    assert LIT[WRITE].name() in colours(write) and LIT[CARD].name() not in colours(write)  # fmt: skip
    assert LIT[CARD].name() in colours(card) and LIT[WRITE].name() not in colours(card)  # fmt: skip


def test_an_unanalysed_track_is_a_dashed_line_and_a_track_with_none_is_empty(qapp):
    pending = sized(CdjWaveStrip(None, PENDING))
    assert DASH.name() in colours(pending)
    assert LIT[WRITE].name() not in colours(pending)
    none = sized(CdjWaveStrip(None, NONE))
    seen = colours(none)
    assert LIT[WRITE].name() not in seen and LIT[CARD].name() not in seen
    assert DASH.name() not in seen


def test_the_look_is_the_players_not_the_themes(qapp):
    from dubcrate.ui import theme

    accents = set()
    for name in ("dark", "light"):
        theme.apply_to_app(qapp, name)
        accents.add(QColor(theme.current_palette()["accent"]).name())
        seen = colours(sized(CdjWaveStrip(saw(), WRITE)))
        assert (
            LIT[WRITE].name() in seen and PANEL.name() in seen
        )  # (the same panel and dots in each)
        assert QColor(theme.current_palette()["accent"]).name() not in seen
    theme.apply_to_app(qapp, "dark")
    assert len(accents) == 2  # (the themes did differ)


def test_a_strip_can_be_given_new_values(qapp):
    strip = CdjWaveStrip(None, PENDING)
    assert strip.values == [] and strip.state == PENDING
    strip.set_values(saw(), WRITE)
    assert strip.values == saw() and strip.state == WRITE


# -- which waveform each track shows ---------------------------------------------------------------


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def disc_of(lib, tmp_path, n=3, analysed=True):
    paths = []
    for k in range(n):
        p = tmp_path / f"t{k}.flac"
        p.write_bytes(b"x")
        paths.append(p)
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=f"t{k}", path=str(p), title=f"t{k}", artist="A", duration=100.0, file_size=1) for k, p in enumerate(paths)],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    for track_id in ids:
        lib.set_cd_sectors(track_id, 75 * 60)
        if analysed:
            lib.set_waveform(track_id, saw())
    disc = lib.create_disc("D")
    lib.add_to_disc(disc, ids)
    lib.mark_burned(disc)
    return disc


def survey_of(lib, disc, card=None, waves=True):
    return card_discs.survey_discs(
        lib, card, card_discs.RekordboxCues(lib), {disc: "D"}, waves=waves
    )[0]


def test_a_track_to_be_written_shows_its_own_waveform(lib, tmp_path):
    disc = disc_of(lib, tmp_path)
    survey = survey_of(lib, disc, (cc.IndexFile(), {}))
    rows = card_discs.wave_rows(survey, lib.disc_entries(disc))
    assert [r.state for r in rows] == [card_discs.WAVE_WRITE] * 3
    assert [r.number for r in rows] == [1, 2, 3]
    assert rows[0].values == tuple(saw()) and rows[0].note == "will be written"
    assert rows[0].title == "A – t0"


def test_a_track_the_card_already_has_shows_the_cards_own_and_is_kept(lib, tmp_path):
    disc = disc_of(lib, tmp_path)
    request = card_discs.prepare_disc(lib, disc, waves=True).request
    theirs = saw(7)
    index = cc.IndexFile()
    entry = index.add(request.disc_id)
    file = cc.TrackFile.new(request.disc_id)
    file.coarse, file.fine = (
        [wave_model.records(1, theirs)[0]],
        [wave_model.records(1, theirs)[1]],
    )
    survey = survey_of(lib, disc, (index, {entry.name: file}))
    rows = card_discs.wave_rows(survey, lib.disc_entries(disc))
    assert [r.state for r in rows] == [
        card_discs.WAVE_CARD,
        card_discs.WAVE_WRITE,
        card_discs.WAVE_WRITE,
    ]
    assert (
        rows[0].values == tuple(theirs) and "kept" in rows[0].note
    )  # (the player's, not ours)


def test_a_track_not_analysed_yet_says_it_will_be(lib, tmp_path):
    disc = disc_of(lib, tmp_path, analysed=False)
    survey = survey_of(lib, disc, (cc.IndexFile(), {}))
    rows = card_discs.wave_rows(survey, lib.disc_entries(disc))
    assert {r.state for r in rows} == {card_discs.WAVE_PENDING}
    assert "when you sync" in rows[0].note and rows[0].values == ()


def test_with_no_card_a_track_shows_what_was_analysed_and_with_waveforms_off_nothing(
    lib, tmp_path
):
    disc = disc_of(lib, tmp_path)
    survey = survey_of(lib, disc, None)
    rows = card_discs.wave_rows(survey, lib.disc_entries(disc))
    assert rows[0].state == card_discs.WAVE_WRITE and "choose a card" in rows[0].note
    off = card_discs.wave_rows(survey_of(lib, disc, (cc.IndexFile(), {}), waves=False), lib.disc_entries(disc), enabled=False)  # fmt: skip
    assert {r.state for r in off} == {card_discs.WAVE_NONE} and off[
        0
    ].note == "Write waveforms is off"


def test_a_track_whose_file_is_missing_says_so(lib, tmp_path):
    disc = disc_of(lib, tmp_path, analysed=False)
    (tmp_path / "t1.flac").unlink()
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=f"t{k}", path=str(tmp_path / f"t{k}.flac"), title=f"t{k}", artist="A", duration=100.0, file_size=1) for k in range(3)],
    )  # fmt: skip
    survey = survey_of(lib, disc, (cc.IndexFile(), {}))
    rows = card_discs.wave_rows(survey, lib.disc_entries(disc))
    assert (
        rows[1].state == card_discs.WAVE_NONE and rows[1].note == "its file is missing"
    )
