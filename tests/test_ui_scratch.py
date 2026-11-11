"""Scratching the zoomed waveform, and beat jump on the arrow keys."""

import dataclasses
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from dubcrate import settings
from dubcrate.analysis import Analysis
from dubcrate.audio.decode import DecodedAudio
from dubcrate.audio.devices import SAMPLE_RATE
from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.preview import PreviewController
from dubcrate.ui.settings_dialog import SettingsDialog
from dubcrate.ui.track_table import TrackTable
from dubcrate.ui.waveform_view import DetailView
from dubcrate.waveform import Waveform


def waveform(seconds=20.0, rate=150.0) -> Waveform:
    columns = int(seconds * rate)
    return Waveform(np.full((columns, 3), 0.5, dtype=np.float32), rate)


def detail(width=800, seconds=20.0) -> DetailView:
    view = DetailView()
    view.resize(width, view.height())
    view.set_track(
        waveform(seconds), np.arange(0, seconds, 0.5), np.tile([1, 2, 3, 4], 100)
    )
    return view  # 8 s across 800 px: 100 px a second


def press(view, x):
    QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=QPoint(x, 20))


def move(view, x):
    QTest.mouseMove(view, QPoint(x, 20))


def release(view, x):
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=QPoint(x, 20))


def events(view) -> SimpleNamespace:
    seen = SimpleNamespace(log=[], moved=[])
    view.scratch_started.connect(lambda: seen.log.append("start"))
    view.scratch_moved.connect(
        lambda s: (seen.log.append("move"), seen.moved.append(s))
    )
    view.scratch_ended.connect(lambda: seen.log.append("end"))
    return seen


# -- the detail view: grab it and drag ----------------------------------------


def test_dragging_pulls_the_track_under_the_pointer(qapp):
    view = detail()
    view.set_position(10.0)
    seen = events(view)
    press(view, 400)
    move(view, 500)  # 100 px right at 100 px a second: the track goes back a second
    move(view, 200)  # then 200 px left of where it started: forward 2 s
    release(view, 200)
    assert seen.log == ["start", "move", "move", "end"]
    assert seen.moved == [pytest.approx(9.0), pytest.approx(12.0)]


def test_the_waveform_follows_the_hand_not_the_player(qapp):
    view = detail()
    view.set_position(10.0)
    press(view, 400)
    move(view, 300)
    assert view._position == pytest.approx(11.0)
    view.set_position(10.0)  # the audio's own (lagging) position arrives: ignored
    assert view._position == pytest.approx(11.0) and view.dragging
    release(view, 300)
    assert not view.dragging
    view.set_position(10.5)  # and is listened to again once the hand is off
    assert view._position == 10.5


def test_a_click_that_does_not_move_grabs_and_lets_go_without_seeking(qapp):
    view = detail()
    view.set_position(10.0)
    seen = events(view)
    press(view, 600)
    release(view, 600)
    assert seen.log == ["start", "end"]
    assert view._position == 10.0  # no jump to where it was clicked


def test_dragging_cannot_go_before_the_start_or_past_the_end(qapp):
    view = detail(seconds=20.0)
    view.set_position(1.0)
    seen = events(view)
    press(view, 400)
    move(view, 700)  # would be 3 s back from 1 s
    assert seen.moved[-1] == 0.0
    release(view, 700)
    view.set_position(19.0)
    press(view, 400)
    move(view, 0)  # 4 s forward from 19 s
    assert seen.moved[-1] == pytest.approx(20.0)
    release(view, 0)


def test_the_drag_follows_the_zoom_level(qapp):
    view = detail()
    view.set_position(10.0)
    view.zoom(1)  # 4 s across 800 px: 200 px a second
    seen = events(view)
    press(view, 400)
    move(view, 200)  # 200 px left
    assert seen.moved == [pytest.approx(11.0)]
    release(view, 200)


def test_only_the_left_button_scratches(qapp):
    view = detail()
    seen = events(view)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(400, 20), QPointF(400, 20),
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
    )  # fmt: skip
    view.mousePressEvent(event)
    assert seen.log == [] and not view.dragging


def test_with_no_track_loaded_there_is_nothing_to_grab(qapp):
    view = DetailView()
    view.resize(800, view.height())
    seen = events(view)
    press(view, 400)
    release(view, 400)
    assert seen.log == [] and not view.dragging


def test_the_cursor_is_an_open_hand_that_closes_while_dragging(qapp):
    view = detail()
    assert view.cursor().shape() == Qt.CursorShape.OpenHandCursor
    press(view, 400)
    assert view.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    release(view, 400)
    assert view.cursor().shape() == Qt.CursorShape.OpenHandCursor


def test_a_new_track_drops_a_drag_in_progress(qapp):
    view = detail()
    press(view, 400)
    view.set_track(waveform())  # loaded while the hand is still down
    assert not view.dragging
    seen = events(view)
    release(view, 400)
    assert seen.log == []  # nothing to end


# -- the strip -----------------------------------------------------------------


def test_the_strip_forwards_the_scratch_and_shows_where_the_hand_has_taken_it(qapp):
    strip = PlayerStrip()
    strip.show_media(20.0, waveform(), None)
    strip.detail.resize(800, strip.detail.height())  # 100 px a second at the 8 s zoom
    log = []
    strip.scratch_started.connect(lambda: log.append("start"))
    strip.scratch_moved.connect(lambda s: log.append(round(s, 3)))
    strip.scratch_ended.connect(lambda: log.append("end"))
    strip.set_position(10.0)
    press(strip.detail, 400)
    move(strip.detail, 300)
    assert log == ["start", 11.0]
    assert strip.elapsed.text() == "0:11" and strip.overview._position == pytest.approx(
        11.0
    )
    strip.set_position(10.0)  # the player's lagging position must not drag it back
    assert strip.elapsed.text() == "0:11" and strip.overview._position == pytest.approx(
        11.0
    )
    release(strip.detail, 300)
    assert log[-1] == "end"
    strip.set_position(11.5)
    assert strip.elapsed.text() == "0:11" and strip.overview._position == 11.5


def test_clearing_the_strip_mid_drag_resets_the_clock(qapp):
    strip = PlayerStrip()
    strip.show_media(20.0, waveform(), None)
    strip.detail.resize(800, strip.detail.height())  # 100 px a second at the 8 s zoom
    strip.set_position(10.0)
    press(strip.detail, 400)
    move(strip.detail, 300)
    strip.clear()
    assert strip.elapsed.text() == "0:00" and strip.overview._position == 0.0


# -- scratching, all the way to the audio --------------------------------------


@pytest.fixture
def chain(qapp):
    """A strip, a controller and a real player (on the test suite's fake device)."""
    lib = Library.open(":memory:")
    strip = PlayerStrip()
    player = AudioPlayer()
    controller = PreviewController(lib, strip, player)
    samples = np.zeros((SAMPLE_RATE * 20, 2), dtype=np.int16)
    samples[:, 0] = np.arange(len(samples)) % 20_000
    player.load(DecodedAudio(samples))
    controller._track = _track()
    strip.show_media(20.0, waveform(), None)
    strip.detail.resize(800, strip.detail.height())  # 100 px a second at the 8 s zoom
    yield SimpleNamespace(strip=strip, controller=controller, player=player)
    controller.shutdown()
    lib.close()


def _track(**kw):
    from dubcrate.library.models import Track

    base = {"id": 1, "source_id": 1, "ext_id": "x", "path": "/x.mp3", "title": "T",
            "artist": "A", "bpm": None, "duration": 20.0}  # fmt: skip
    return Track(**{**base, **kw})


def test_dragging_the_waveform_scratches_the_audio_and_lands_where_it_was_dropped(
    chain,
):
    player, strip = chain.player, chain.strip
    player.seek(10.0)
    strip.set_position(10.0)
    press(strip.detail, 400)
    assert player.scratching
    move(strip.detail, 300)  # one second forward
    device = (
        player.opened_device if hasattr(player, "opened_device") else player._device
    )
    heard = []
    for _ in range(60):
        heard.append(bool(np.frombuffer(device.pull(1024), np.int16).any()))
    assert any(heard)  # the scratch was audible
    release(strip.detail, 300)
    assert not player.scratching
    assert player.position == pytest.approx(11.0, abs=0.003)
    assert not player.playing  # it was paused, and stays paused


def test_scratching_a_playing_track_resumes_playing_from_the_drop(chain):
    player, strip = chain.player, chain.strip
    player.seek(5.0)
    player.play()
    device = player._device
    device.pull(1024)
    strip.set_position(player.position)
    press(strip.detail, 400)
    move(strip.detail, 500)  # back one second
    for _ in range(60):
        device.pull(1024)
    release(strip.detail, 500)
    assert player.playing
    landed = player.position
    for _ in range(4):
        device.pull(1024)
    assert player.position > landed  # playing on from the drop


# -- beat jump -----------------------------------------------------------------


@pytest.fixture
def jumper(chain):
    chain.controller._analysis = Analysis(beat_times=np.arange(0, 20, 0.5))
    chain.messages = []
    chain.controller.message.connect(chain.messages.append)
    return chain


def test_the_arrow_keys_jump_four_beats_by_default(jumper):
    jumper.player.seek(4.0)
    jumper.controller.beat_jump(1)
    assert jumper.player.position == pytest.approx(6.0)  # 4 beats at 120 BPM
    jumper.controller.beat_jump(-1)
    jumper.controller.beat_jump(-1)
    assert jumper.player.position == pytest.approx(2.0)


def test_a_jump_keeps_its_place_within_the_beat(jumper):
    jumper.player.seek(4.2)
    jumper.controller.beat_jump(1)
    assert jumper.player.position == pytest.approx(6.2)


def test_the_jump_size_is_a_setting(jumper):
    settings.set_beat_jump(16)
    jumper.player.seek(0.0)
    jumper.controller.beat_jump(1)
    assert jumper.player.position == pytest.approx(8.0)


def test_a_jump_is_clamped_to_the_track(jumper):
    jumper.player.seek(1.0)
    jumper.controller.beat_jump(-1)
    assert jumper.player.position == 0.0
    jumper.player.seek(19.0)
    jumper.controller.beat_jump(1)
    assert jumper.player.position == pytest.approx(20.0)


def test_a_jump_while_playing_keeps_playing(jumper):
    jumper.player.seek(2.0)
    jumper.player.play()
    jumper.controller.beat_jump(1)
    assert jumper.player.playing and jumper.player.position == pytest.approx(4.0)


def test_without_a_beat_grid_the_tracks_tempo_is_used(chain):
    chain.controller._analysis = None
    chain.controller._track = _track(bpm=120.0)
    chain.player.seek(3.0)
    chain.controller.beat_jump(1)
    assert chain.player.position == pytest.approx(5.0)  # 4 beats x 0.5 s


def test_rekordboxs_own_tempo_is_preferred_to_the_tag(chain):
    chain.controller._analysis = Analysis(bpm=60.0)  # no grid, but a tempo
    chain.controller._track = _track(bpm=120.0)
    chain.player.seek(3.0)
    chain.controller.beat_jump(1)
    assert chain.player.position == pytest.approx(7.0)  # 4 beats x 1 s


def test_with_no_grid_and_no_tempo_it_says_why_and_stays_put(chain):
    messages = []
    chain.controller.message.connect(messages.append)
    chain.controller._analysis = None
    chain.player.seek(3.0)
    chain.controller.beat_jump(1)
    assert chain.player.position == 3.0
    assert "can't beat jump" in messages[0]


def test_beat_jump_does_nothing_with_no_track_or_during_a_scratch(qapp):
    lib = Library.open(":memory:")
    controller = PreviewController(lib, PlayerStrip(), AudioPlayer())
    controller.beat_jump(1)  # nothing loaded: nothing happens, no error
    controller.shutdown()
    lib.close()


def test_a_jump_is_ignored_while_the_waveform_is_held(jumper):
    jumper.player.seek(4.0)
    jumper.player.begin_scratch()
    jumper.controller.beat_jump(1)
    jumper.player.end_scratch()
    assert jumper.player.position == pytest.approx(4.0)


def test_loading_another_track_forgets_the_old_beat_grid(jumper):
    track = dataclasses.replace(_track(), path="/nowhere/other.mp3", id=2)
    jumper.controller.load(
        track, play=False
    )  # starts a load: the old analysis is stale
    assert jumper.controller._analysis is None


# -- the keys and the setting --------------------------------------------------


def key(table, k, modifiers=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(table, k, modifiers)


def test_left_and_right_on_the_track_list_ask_for_a_beat_jump(qapp):
    table = TrackTable()
    got = []
    table.beat_jump_requested.connect(got.append)
    key(table, Qt.Key.Key_Left)
    key(table, Qt.Key.Key_Right)
    key(table, Qt.Key.Key_Right)
    assert got == [-1, 1, 1]


def test_arrow_keys_with_a_modifier_keep_their_normal_meaning(qapp):
    table = TrackTable()
    got = []
    table.beat_jump_requested.connect(got.append)
    key(table, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    key(table, Qt.Key.Key_Left, Qt.KeyboardModifier.ControlModifier)
    key(table, Qt.Key.Key_Down)
    assert got == []


def test_the_default_beat_jump_is_four_beats_and_nonsense_falls_back(qapp):
    assert settings.get_beat_jump() == 4
    settings._qs().setValue("playback/beat_jump", 7)
    assert settings.get_beat_jump() == 4
    settings._qs().setValue("playback/beat_jump", "lots")
    assert settings.get_beat_jump() == 4


def test_the_beat_jump_size_is_chosen_in_the_audio_settings(qapp):
    dialog = SettingsDialog()
    assert dialog.beat_jump_combo.currentData() == 4
    dialog.beat_jump_combo.setCurrentIndex(dialog.beat_jump_combo.findData(8))
    dialog.beat_jump_combo.activated.emit(dialog.beat_jump_combo.currentIndex())
    assert settings.get_beat_jump() == 8
    assert SettingsDialog().beat_jump_combo.currentData() == 8
