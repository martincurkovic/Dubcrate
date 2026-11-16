"""The player laid out as rbxport's is: left controls, zoom, the cue row, the side panel, and dropping a track on it."""

import itertools
import json

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent

from dubcrate import settings
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import player_side
from dubcrate.ui.models import TRACK_MIME
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.waveform_view import DEFAULT_ZOOM, ZOOMS

CUES = (
    Cue("memory", 0.0),
    Cue("memory", 31.5),
    Cue("loop", 60.0, end=64.0),
    Cue("hot", 29.25, slot=2),
    Cue("hot", 77.933, slot=1),
    Cue("hot", 200.0, slot=4),
)


@pytest.fixture
def strip(qapp):
    widget = PlayerStrip()
    widget.resize(1300, 340)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.deleteLater()


def jumps(strip):
    heard = []
    strip.jump_requested.connect(heard.append)
    return heard


def test_a_hot_cue_button_is_lit_only_when_the_track_has_that_cue_and_jumps_there(strip):  # fmt: skip
    assert not any(b.isEnabled() for b in strip.hot_buttons)  # (nothing loaded)
    strip.show_cues(CUES)
    lit = [b.text() for b in strip.hot_buttons if b.isEnabled()]
    assert lit == ["A", "B", "D"]
    heard = jumps(strip)
    strip.hot_buttons[0].click()  # A
    strip.hot_buttons[3].click()  # D
    assert heard == [77.933, 200.0]
    strip.hot_buttons[2].click()  # C: not set, disabled: a click does nothing
    assert heard == [77.933, 200.0]
    strip.clear()
    assert not any(b.isEnabled() for b in strip.hot_buttons)


def test_the_memory_arrows_go_to_the_nearest_memory_cue_either_side(strip):
    strip.show_cues(CUES)
    heard = jumps(strip)
    strip.set_position(10.0)
    strip.memory_next_button.click()
    strip.memory_back_button.click()
    assert heard == [31.5, 0.0]
    strip.set_position(31.5)  # on a cue: the next one is the one after it
    strip.memory_next_button.click()
    assert (
        heard[-1] == 60.0 or len(heard) == 2
    )  # (a loop is kept in the memory list: 60 s)
    strip.set_position(100.0)
    before = len(heard)
    strip.memory_next_button.click()  # none after: stays
    assert len(heard) == before
    strip.show_cues(())
    assert not strip.memory_back_button.isEnabled() and not strip.memory_next_button.isEnabled()  # fmt: skip


def test_the_memory_buttons_wait_for_a_track_and_say_rekordbox_is_not_changed(strip):
    for button in (strip.memory_button, strip.memory_delete_button):
        assert not button.isEnabled() and "rekordbox" in button.toolTip()
    strip._enable(True)
    for button in (strip.memory_button, strip.memory_delete_button):
        assert button.isEnabled()
    heard = []
    strip.memory_add_requested.connect(lambda: heard.append("add"))
    strip.memory_delete_requested.connect(lambda: heard.append("delete"))
    strip.memory_button.click()
    strip.memory_delete_button.click()
    assert heard == ["add", "delete"]


def test_the_loop_cue_and_quantise_buttons_are_off_until_a_track_is_loaded_and_have_tips(strip):  # fmt: skip
    buttons = (strip.loop_in, strip.loop_out, strip.reloop, strip.loop_size, strip.quantise_button, strip.cue_button)  # fmt: skip
    assert not any(b.isEnabled() for b in buttons)
    assert all(b.toolTip() for b in buttons)
    strip._enable(True)
    assert all(b.isEnabled() for b in buttons)


def test_au_and_ma_swap_the_loop_controls_and_only_the_shown_one_takes_room(strip):
    assert strip.auto_button.isChecked() and not strip.manual_button.isChecked()
    assert strip.loop_auto_group.isVisibleTo(strip)
    assert not strip.loop_manual_group.isVisibleTo(strip)
    strip.manual_button.click()
    assert strip.manual_button.isChecked() and not strip.auto_button.isChecked()
    assert strip.loop_manual_group.isVisibleTo(strip)
    assert not strip.loop_auto_group.isVisibleTo(strip)
    strip.auto_button.click()
    assert strip.loop_auto_group.isVisibleTo(strip)
    assert not strip.loop_manual_group.isVisibleTo(strip)


def test_the_side_panel_lists_memory_cues_loops_and_hot_cues(strip):
    strip.show_cues(CUES)
    side = strip.side
    memory = [side.memory.topLevelItem(i).text(0) for i in range(side.memory.topLevelItemCount())]  # fmt: skip
    assert memory == ["00:00.000", "00:31.500", "01:00.000"]
    # (a loop is its start time with a loop mark after it; where it ends is its tooltip)
    from dubcrate.ui.player_side import LOOP_ROLE

    marks = [side.memory.topLevelItem(i).data(0, LOOP_ROLE) for i in range(3)]
    assert marks == [False, False, True]
    assert side.memory.topLevelItem(2).toolTip(0) == "Loop: 01:00.000 to 01:04.000"
    hot = [(side.hot.topLevelItem(i).text(0), side.hot.topLevelItem(i).text(1)) for i in range(8)]  # fmt: skip
    assert hot[0] == ("A", "01:17.933") and hot[1] == ("B", "00:29.250")
    assert hot[2] == ("C", "") and hot[3] == ("D", "03:20.000") and hot[7] == ("H", "")
    assert side.hot.columnCount() == 2 and not side.hot.isColumnHidden(
        1
    )  # (the times show)
    assert side.hot.columnWidth(1) > 40
    heard = jumps(strip)
    side.memory.itemClicked.emit(side.memory.topLevelItem(1), 0)
    side.hot.itemClicked.emit(side.hot.topLevelItem(0), 0)
    side.hot.itemClicked.emit(side.hot.topLevelItem(2), 0)  # (C is empty: nothing)
    assert heard == [31.5, 77.933]


def test_the_tabs_show_memory_hot_cues_and_info_and_start_on_memory(strip):
    side = strip.side
    assert [b.text() for b in side.tabs] == ["MEMORY", "HOT CUE", "INFO"]
    assert side.pages.currentIndex() == 0 and side.tabs[0].isChecked()
    side.tabs[1].click()
    assert side.pages.currentIndex() == 1 and not side.tabs[0].isChecked()
    side.tabs[2].click()
    assert side.pages.currentIndex() == 2


def test_the_info_tab_shows_rating_comment_and_what_the_file_is(strip):
    with Library.open(":memory:") as lib:
        lib.replace_source(
            "folder",
            "/m",
            "M",
            [
                TrackRecord(
                    ext_id="a",
                    path="/m/a.mp3",
                    title="T",
                    artist="A",
                    duration=120.0,
                    rating=4,
                    comment="Opener, clean",
                    bitrate=320,
                    sample_rate=44100,
                    file_size=11_900_000,
                )
            ],
        )
        track = lib.tracks()[0]
    strip.show_track(track)
    side = strip.side
    assert side.rating.text() == "★★★★☆" and side.comment.text() == "Opener, clean"
    assert side.file.text().splitlines() == [
        "MP3 File",
        "11.9 MB",
        "44100 Hz",
        "320 kbps",
    ]
    strip.clear()
    assert side.rating.text() == "" and side.file.text() == ""


def test_info_text_helpers():
    assert player_side.stars(None) == "☆☆☆☆☆" and player_side.stars(9) == "★★★★★"
    assert player_side.cue_clock(77.9334) == "01:17.933" and player_side.cue_clock(-1) == "00:00.000"  # fmt: skip
    assert player_side.size_text(None) == "" and player_side.size_text(900) == "1 kB"


def test_the_beat_jump_menu_says_beats_then_bars_and_keeps_the_saved_size(strip):
    labels = [strip.jump_combo.itemText(i) for i in range(strip.jump_combo.count())]
    assert labels == ["1 Beat", "2 Beats", "4 Beats", "8 Beats", "16 Beats", "8 Bars", "16 Bars", "32 Bars"]  # fmt: skip
    assert strip.jump_combo.currentData() == settings.get_beat_jump()
    strip.jump_combo.setCurrentIndex(strip.jump_combo.findData(32))
    strip.jump_combo.activated.emit(strip.jump_combo.currentIndex())
    assert settings.get_beat_jump() == 32
    strip.set_beat_jump(4)
    assert strip.jump_combo.currentText() == "4 Beats" and settings.get_beat_jump() == 32  # (shown, not saved)  # fmt: skip


def test_the_jump_buttons_ask_for_a_jump_either_way_and_wait_for_a_track(strip):
    heard = []
    strip.beat_jump_requested.connect(heard.append)
    assert not strip.jump_back_button.isEnabled()
    strip._enable(True)
    strip.jump_back_button.click()
    strip.jump_forward_button.click()
    assert heard == [-1, 1]


def test_zoom_buttons_zoom_the_waveform_and_reset_it(strip):
    detail = strip.detail
    assert detail.window_seconds == DEFAULT_ZOOM
    detail.zoom_in_button.click()
    assert detail.window_seconds < DEFAULT_ZOOM
    detail.zoom_in_button.click()
    detail.zoom_in_button.click()
    assert detail.window_seconds == ZOOMS[0]  # (as far in as it goes)
    detail.zoom_out_button.click()
    assert detail.window_seconds > ZOOMS[0]
    detail.zoom_reset_button.click()
    assert detail.window_seconds == DEFAULT_ZOOM
    # the buttons sit over the waveform's left edge, stacked
    assert all(b.parent() is detail for b in (detail.zoom_in_button, detail.zoom_reset_button, detail.zoom_out_button))  # fmt: skip
    ys = [b.y() for b in (detail.zoom_in_button, detail.zoom_reset_button, detail.zoom_out_button)]  # fmt: skip
    assert ys == sorted(ys) and detail.zoom_out_button.geometry().bottom() < detail.height()  # fmt: skip


def drop(strip, mime):
    event = QDropEvent(QPointF(40, 40), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)  # fmt: skip
    strip.dropEvent(event)
    return event


def test_a_track_dropped_on_the_player_is_asked_for(strip):
    heard = []
    strip.track_dropped.connect(heard.append)
    mime = QMimeData()
    mime.setData(TRACK_MIME, json.dumps([7, 8]).encode())
    assert drop(strip, mime).isAccepted()
    assert heard == [7]  # (the first of those dragged)
    other = QMimeData()
    other.setText("not a track")
    drop(strip, other)
    broken = QMimeData()
    broken.setData(TRACK_MIME, b"not json")
    drop(strip, broken)
    assert heard == [7]
    assert strip.acceptDrops()


def test_the_player_is_wired_to_load_a_dropped_track_and_to_jump(qapp, monkeypatch):
    from dubcrate.ui.main_window import MainWindow

    lib = Library.open(":memory:")
    lib.replace_source(
        "folder", "/m", "M",
        [TrackRecord(ext_id="a", path="/m/a.flac", title="T", duration=10.0)],
    )  # fmt: skip
    win = MainWindow(lib)
    loaded, seeks = [], []
    monkeypatch.setattr(win.preview, "load", loaded.append)
    win.player_strip.track_dropped.emit(lib.tracks()[0].id)
    assert [t.title for t in loaded] == ["T"]
    win.player_strip.track_dropped.emit(99999)  # (a track that is not there: nothing)
    assert len(loaded) == 1
    monkeypatch.setattr(win.preview.player, "seek", seeks.append)
    monkeypatch.setattr(type(win.preview.player), "loaded", property(lambda self: True))
    monkeypatch.setattr(
        type(win.preview.player), "duration", property(lambda self: 100.0)
    )
    win.player_strip.jump_requested.emit(42.0)
    win.player_strip.jump_requested.emit(500.0)  # (past the end: held to it)
    assert seeks == [42.0, 100.0]
    win.close()
    win.deleteLater()
    lib.close()


# -- the look of the left column ---------------------------------------------------------------


def test_the_beat_jump_menu_ticks_the_current_size_and_picking_one_saves_it(strip):
    strip.set_beat_jump(8)
    menu = strip.jump_combo.menu()
    actions = menu.actions()
    assert [a.text() for a in actions][:3] == ["1 Beat", "2 Beats", "4 Beats"]
    assert [a.text() for a in actions if a.isChecked()] == ["8 Beats"]
    picked = []
    strip.jump_combo.activated.connect(picked.append)
    next(a for a in actions if a.text() == "8 Bars").trigger()  # 32 beats
    assert settings.get_beat_jump() == 32 and strip.jump_combo.currentText() == "8 Bars"  # fmt: skip
    assert picked == [strip.jump_combo.findData(32)]
    assert [a.text() for a in strip.jump_combo.menu().actions() if a.isChecked()] == ["8 Bars"]  # fmt: skip
    strip.set_beat_jump(4)  # (the program choosing is not the user picking)
    assert picked == [strip.jump_combo.findData(32)]


def test_the_beat_jump_button_is_as_wide_as_the_column_and_shows_a_menu_caret(strip):
    assert strip.jump_combo.width() == strip.cue_button.parent().width()
    assert strip.jump_combo.text().endswith("▾")


def test_a_button_shape_must_be_one_of_the_four(qapp):
    from dubcrate.ui.player_controls import PillButton

    for shape in ("pill", "tile", "circle", "text"):
        assert PillButton("x", shape=shape).shape == shape
    with pytest.raises(ValueError, match="shape"):
        PillButton("x", shape="star")


def colours_in(widget):
    from PySide6.QtGui import QColor

    image = widget.grab().toImage()
    return {QColor(image.pixel(x, y)).name() for x in range(image.width()) for y in range(image.height())}  # fmt: skip


def test_play_and_cue_are_rings_the_same_size_in_green_and_orange(strip):
    from PySide6.QtGui import QColor

    from dubcrate.ui.player_controls import CUE_RING, PLAY_RING, RING_SIZE

    strip._enable(True)
    assert (strip.play_button.width(), strip.cue_button.width()) == (
        RING_SIZE,
        RING_SIZE,
    )
    assert QColor(PLAY_RING).name() in colours_in(strip.play_button)
    assert QColor(CUE_RING).name() in colours_in(strip.cue_button)
    assert QColor(PLAY_RING).name() not in colours_in(strip.cue_button)
    strip.set_playing(True)  # (brighter while it plays)
    assert QColor(PLAY_RING).name() not in colours_in(strip.play_button)  # lighter(130)


def test_the_zoom_buttons_are_two_rings_and_a_label_in_one_column(strip):
    detail = strip.detail
    shapes = [b.shape for b in (detail.zoom_in_button, detail.zoom_reset_button, detail.zoom_out_button)]  # fmt: skip
    assert shapes == ["circle", "text", "circle"]
    centres = {b.geometry().center().x() for b in (detail.zoom_in_button, detail.zoom_reset_button, detail.zoom_out_button)}  # fmt: skip
    assert max(centres) - min(centres) <= 1


# -- the cue row's layout ----------------------------------------------------------------------


def test_joined_buttons_know_their_place_in_the_group(qapp):
    from dubcrate.ui.player_controls import PillButton, join

    buttons = [PillButton(t) for t in "abcd"]
    join(buttons)
    assert [b.segment for b in buttons] == ["first", "middle", "middle", "last"]
    (alone,) = [PillButton("x")]
    join([alone])
    assert alone.segment == "only"
    with pytest.raises(ValueError, match="segment"):
        alone.set_segment("end")


def test_a_segment_is_rounded_only_at_the_groups_ends(qapp):
    from PySide6.QtCore import QPointF, QRectF

    from dubcrate.ui.player_controls import segment_path

    box = QRectF(0.5, 0.5, 40, 25)
    corner = QPointF(1.0, 1.0)  # just inside the top-left corner
    assert not segment_path(box, 6, first=True, last=False).contains(
        corner
    )  # (rounded away)
    assert segment_path(box, 6, first=False, last=False).contains(corner)  # (square)
    far = QPointF(39.8, 1.0)
    assert not segment_path(box, 6, first=False, last=True).contains(far)
    assert segment_path(box, 6, first=True, last=False).contains(far)


def test_every_button_in_the_cue_row_is_the_same_height(strip):
    from dubcrate.ui.player_strip import ROW_HEIGHT

    row = [*strip.hot_buttons, strip.memory_button, strip.memory_back_button, strip.memory_next_button, strip.memory_delete_button, strip.auto_button, strip.manual_button, strip.loop_size_back, strip.loop_size, strip.loop_size_forward, strip.loop_in, strip.loop_out, strip.reloop, strip.quantise_button]  # fmt: skip
    assert {b.height() for b in row} == {ROW_HEIGHT}


def test_a_joined_group_has_no_gaps_and_takes_no_more_room_than_its_buttons(strip):
    strip.show_cues(())
    groups = [
        (strip.memory_button, strip.memory_back_button, strip.memory_next_button, strip.memory_delete_button),
        (strip.auto_button, strip.manual_button),
        (strip.loop_size_back, strip.loop_size, strip.loop_size_forward),
    ]  # fmt: skip
    for group in groups:
        for left, right in itertools.pairwise(group):
            assert left.geometry().right() + 1 == right.geometry().left()  # (touching)
    assert strip.loop_auto_group.width() == sum(
        b.width() for b in groups[2]
    )  # (the arrows do not drift apart)
    strip.manual_button.click()
    manual = (strip.loop_in, strip.loop_out, strip.reloop)
    assert strip.loop_manual_group.width() == sum(b.width() for b in manual)


def test_the_groups_are_told_apart_by_dividers_and_the_hot_cues_stay_separate(strip):
    from dubcrate.ui.player_controls import GroupDivider

    assert len(strip.findChildren(GroupDivider)) == 2
    xs = [b.geometry().left() for b in strip.hot_buttons]
    gaps = {
        xs[i + 1] - xs[i] - strip.hot_buttons[i].width() for i in range(len(xs) - 1)
    }
    assert gaps == {4}  # (A to H are separate squares, evenly spaced)


# -- the info page: its frame and the cover ----------------------------------------------------------


def png_bytes(width=300, height=200, colour="#cc3344"):
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_the_info_page_has_the_same_kind_of_border_as_the_other_two_pages(strip):
    from PySide6.QtGui import QColor

    from dubcrate.ui import theme
    from dubcrate.ui.player_side import InfoFrame

    side = strip.side
    side.tabs[2].click()
    frame = side.pages.widget(2)
    assert isinstance(frame, InfoFrame) and side.pages.currentWidget() is frame
    image = frame.grab().toImage()
    border = QColor(theme.current_palette()["border_list"]).name()
    edge = {
        QColor(image.pixel(x, image.height() // 2)).name()
        for x in (0, image.width() - 1)
    }
    assert border in edge  # (a line round it, in the lists' border colour)
    assert QColor(image.pixel(image.width() // 2, 0)).name() == border


def test_a_track_without_a_cover_shows_the_placeholder_and_one_with_a_cover_shows_it(
    strip,
):
    art = strip.side.art
    assert not art.has_image  # (the placeholder: a note on a square)
    strip.side.set_artwork(png_bytes())
    assert art.has_image and art.size().width() == art.size().height() == art.SIZE
    strip.side.set_artwork(None)
    assert not art.has_image
    strip.side.set_artwork(b"this is not a picture")
    assert not art.has_image  # (undecodable: the placeholder, never an error)


def test_a_wide_cover_is_cropped_to_a_square_from_the_middle(strip):
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(
        400, 200, QImage.Format.Format_RGB32
    )  # red | green | blue thirds... the middle is green
    painter = QPainter(image)
    painter.fillRect(0, 0, 100, 200, QColor("#ff0000"))
    painter.fillRect(100, 0, 200, 200, QColor("#00ff00"))
    painter.fillRect(300, 0, 100, 200, QColor("#0000ff"))
    painter.end()
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    strip.side.set_artwork(bytes(buffer.data()))
    shot = strip.side.art.grab().toImage()
    middle = QColor(shot.pixel(shot.width() // 2, shot.height() // 2))
    assert middle.green() > 200 and middle.red() < 60 and middle.blue() < 60
    corner = QColor(shot.pixel(shot.width() // 5, shot.height() // 2))
    assert corner.green() > 200  # (the red third was cropped off, not squeezed in)


def test_choosing_a_track_clears_the_old_cover_until_its_own_arrives(strip):
    strip.side.set_artwork(png_bytes())
    track = make_track()
    strip.show_track(track)
    assert not strip.side.art.has_image
    strip.show_media(60.0, sample_waveform(), None, artwork=png_bytes())
    assert strip.side.art.has_image
    strip.clear()
    assert not strip.side.art.has_image


def make_track():
    from dubcrate.library.models import Track

    return Track(
        id=1,
        source_id=1,
        ext_id="x",
        path="/m/a.mp3",
        title="T",
        artist="A",
        duration=60.0,
    )


def sample_waveform():
    import numpy as np

    from dubcrate.waveform import Waveform

    return Waveform(np.random.default_rng(1).random((600, 3)).astype(np.float32), 150.0)


def test_the_preview_reads_the_cover_with_the_audio_and_shows_it(
    qapp, tmp_path, wait_for, monkeypatch
):
    import wave

    import numpy as np

    from dubcrate.audio.player import AudioPlayer
    from dubcrate.ui import preview

    path = tmp_path / "a.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44_100)
        w.writeframes(np.zeros((22_050, 2), dtype=np.int16).tobytes())
    seen = []
    cover = png_bytes()
    monkeypatch.setattr(preview, "read_artwork", lambda p: seen.append(p) or cover)
    lib = Library.open(":memory:")
    lib.replace_source("folder", str(tmp_path), "S", [TrackRecord(ext_id="a", path=str(path), title="A", duration=0.5)])  # fmt: skip
    widget = PlayerStrip()
    controller = preview.PreviewController(lib, widget, AudioPlayer())
    controller.load(lib.tracks()[0], play=False)
    assert wait_for(lambda: controller.track is not None and widget.side.art.has_image)
    assert seen == [str(path)]
    controller.shutdown()
    lib.close()


def test_the_beat_jump_glyphs_and_amount_are_drawn_bigger_than_an_ordinary_label(qapp):
    from dubcrate.ui.player_controls import PillButton

    def lit(delta):
        button = PillButton("‹", width=50, height=30, shape="tile", size_delta=delta)
        button.show()
        image = button.grab().toImage()
        background = image.pixel(2, 2)
        return sum(1 for x in range(image.width()) for y in range(image.height()) if image.pixel(x, y) != background)  # fmt: skip

    # (the size really changes: pixel-sized app fonts once clamped every label to 8 points)
    assert lit(8) > 1.25 * lit(-1)


def test_the_left_column_sits_a_little_way_down_and_cue_and_play_float_above_the_bottom(
    strip,
):
    top = strip.prev_button.mapTo(strip, strip.prev_button.rect().topLeft()).y()
    assert top >= 18  # (the group is lower than the very top of the strip)
    amount_bottom = strip.jump_combo.mapTo(
        strip, strip.jump_combo.rect().bottomLeft()
    ).y()
    cue_top = strip.cue_button.mapTo(strip, strip.cue_button.rect().topLeft()).y()
    play_bottom = strip.play_button.mapTo(
        strip, strip.play_button.rect().bottomLeft()
    ).y()
    assert cue_top - amount_bottom > 8  # (room between the two groups)
    assert strip.height() - play_bottom > 20  # (not hard against the bottom edge)
