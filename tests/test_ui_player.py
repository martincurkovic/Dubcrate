import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem

from dubcrate import settings
from dubcrate.analysis import Analysis, Cue, Phrase
from dubcrate.audio import devices
from dubcrate.audio.decode import DecodedAudio
from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library, TrackRecord
from dubcrate.library.models import Track
from dubcrate.ui import main_window, preview, theme
from dubcrate.ui.main_window import MainWindow
from dubcrate.ui.models import (
    PREVIEW_COLUMN,
    PREVIEW_ROLE,
    TRACK_COLUMNS,
    TrackTableModel,
)
from dubcrate.ui.player_strip import IDLE_HINT, PlayerStrip, clock
from dubcrate.ui.preview import PreviewController, load_media
from dubcrate.ui.settings_dialog import TONE_LEVEL, SettingsDialog, make_test_tone
from dubcrate.ui.track_table import PreviewDelegate, TrackTable, render_preview
from dubcrate.ui.waveform_view import ZOOMS, DetailView, OverviewView
from dubcrate.waveform import Waveform


def write_wav(path, seconds=1.5, rate=44_100):
    t = np.arange(int(rate * seconds)) / rate
    data = (np.sin(2 * np.pi * 220 * t) * 9_000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())
    return str(path)


def sample_waveform(columns=600, rate=150.0, seed=3):
    bands = np.random.default_rng(seed).random((columns, 3)).astype(np.float32) * [
        1.0,
        0.7,
        0.4,
    ]
    return Waveform(bands.astype(np.float32), rate)


def make_track(**kw) -> Track:
    base = {"id": 1, "source_id": 1, "ext_id": "x", "path": "/nowhere/x.mp3", "title": "Song", "artist": "Artist",
                "bpm": 128.0, "key": "8A", "duration": 200.0}  # fmt: skip
    return Track(**{**base, **kw})


def mouse(
    event_type, x, buttons=Qt.MouseButton.LeftButton, button=Qt.MouseButton.LeftButton
):
    return QMouseEvent(
        event_type,
        QPointF(x, 10),
        QPointF(x, 10),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def pixels(widget) -> QImage:
    return widget.grab().toImage()


def has_content(image: QImage, background) -> bool:
    return any(
        image.pixelColor(x, y) != background
        for x in range(0, image.width(), 3)
        for y in range(0, image.height(), 3)
    )


# -- the player strip ---------------------------------------------------------------------


def test_the_strip_starts_idle_with_nothing_to_play(qapp):
    strip = PlayerStrip()
    assert strip.title.text() == IDLE_HINT and not strip.play_button.isEnabled()
    assert strip.elapsed.text() == "0:00" and strip.remaining.text() == "-0:00"


def test_choosing_a_track_shows_who_it_is_while_it_loads(qapp):
    strip = PlayerStrip()
    strip.show_track(make_track(bpm=130.0, key="9A"))
    assert (
        strip.title.text(),
        strip.artist.text(),
        strip.key.text(),
        strip.bpm.text(),
    ) == ("Song", "Artist", "9A", "130.00")
    assert not strip.play_button.isEnabled()
    assert strip.detail._message == "Loading…"


def test_a_loaded_track_enables_play_and_the_clock_follows_the_position(qapp):
    strip = PlayerStrip()
    strip.show_track(make_track(duration=200.0))
    strip.show_media(200.0, sample_waveform(), Analysis())
    assert strip.play_button.isEnabled()
    strip.set_position(65.4)
    assert strip.elapsed.text() == "1:05" and strip.remaining.text() == "-2:14"
    assert strip.overview._position == 65.4 and strip.detail._position == 65.4


def test_the_play_button_shows_pause_while_playing(qapp):
    strip = PlayerStrip()
    strip.show()
    shown = strip.play_button.grab().toImage()
    assert not strip.play_button.playing
    strip.set_playing(True)
    assert strip.play_button.playing
    assert strip.play_button.grab().toImage() != shown  # it draws pause now, not play
    strip.set_playing(False)
    assert not strip.play_button.playing
    assert strip.play_button.grab().toImage() == shown


def test_the_controls_emit_their_signals(qapp):
    strip = PlayerStrip()
    seen = []
    strip.previous_requested.connect(lambda: seen.append("prev"))
    strip.next_requested.connect(lambda: seen.append("next"))
    strip.play_toggled.connect(lambda: seen.append("play"))
    strip.volume_changed.connect(lambda v: seen.append(("vol", v)))
    strip.show_media(10.0, sample_waveform(), None)
    strip.prev_button.click()
    strip.next_button.click()
    strip.play_button.click()
    strip.volume.setValue(25)
    strip.set_volume(
        0.9
    )  # setting it from outside must not look like the user moved it
    assert (
        seen == ["prev", "next", "play", ("vol", 0.25)] and strip.volume.value() == 90
    )


def test_an_error_replaces_the_waveforms_and_clear_resets_everything(qapp):
    strip = PlayerStrip()
    strip.show_track(make_track())
    strip.show_error("File not found")
    assert (
        strip.detail._message == "File not found" and not strip.play_button.isEnabled()
    )
    strip.clear()
    assert (
        strip.title.text() == IDLE_HINT
        and strip.artist.text() == ""
        and strip.overview._duration == 0.0
    )


def test_clock_format():
    assert [clock(s) for s in (0, 5, 65, 3599, -3)] == [
        "0:00",
        "0:05",
        "1:05",
        "59:59",
        "0:00",
    ]


# -- the waveform widgets -------------------------------------------------------------------


def overview(width=1000, duration=100.0, **kw):
    view = OverviewView()
    view.resize(width, view.height())
    view.set_track(duration, sample_waveform(), **kw)
    return view


def test_clicking_the_overview_seeks_proportionally(qapp):
    view = overview(1000, 100.0)
    got = []
    view.seek_requested.connect(got.append)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(250, 20))
    assert got == [25.0]
    view.mouseMoveEvent(mouse(QEvent.Type.MouseMove, 900))
    view.mouseMoveEvent(
        mouse(QEvent.Type.MouseMove, 5000)
    )  # dragged past the end: clamped
    assert got[1:] == [90.0, 100.0]
    view.mouseMoveEvent(
        mouse(
            QEvent.Type.MouseMove,
            500,
            buttons=Qt.MouseButton.NoButton,
            button=Qt.MouseButton.NoButton,
        )
    )
    assert len(got) == 3  # a move without a button down is not a seek


def test_an_empty_overview_does_not_seek(qapp):
    view = OverviewView()
    view.resize(500, view.height())
    got = []
    view.seek_requested.connect(got.append)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=QPoint(100, 20))
    assert got == [] and view.time_at(100) == 0.0


@pytest.mark.parametrize("preference", ["dark", "light", "system6"])
def test_the_overview_draws_the_waveform_phrases_and_cues_in_every_theme(
    qapp, preference
):
    try:
        theme.set_theme_preference(preference)
        bg = theme.current_palette()["wave_bg"]
        view = overview(
            phrases=(Phrase(0, 40, "INTRO"), Phrase(40, 100, "CHORUS")),
            cues=(
                Cue("hot", 10.0, slot=1),
                Cue("memory", 50.0),
                Cue("loop", 70.0, 72.0),
            ),
        )
        view.set_position(30.0)
        assert has_content(
            pixels(view),
            QImage(1, 1, QImage.Format.Format_RGB32).pixelColor(0, 0).__class__(bg),
        )
        with_nothing = OverviewView()
        with_nothing.resize(1000, with_nothing.height())
        pixels(with_nothing)  # an empty one still paints
    finally:
        theme.set_theme_preference("dark")


def test_playing_dims_what_has_been_played(qapp):
    view = overview(500, 100.0)
    view.set_position(0.0)
    fresh = pixels(view).pixelColor(100, 30)
    view.set_position(90.0)
    assert pixels(view).pixelColor(100, 30) != fresh


def detail(width=800):
    view = DetailView()
    view.resize(width, view.height())
    view.set_track(
        sample_waveform(columns=3000), np.arange(0, 20, 0.5), np.tile([1, 2, 3, 4], 10)
    )
    return view


def test_the_detail_view_keeps_the_playhead_in_the_middle(qapp):
    view = detail(800)
    view.set_position(10.0)
    assert view.time_at(400) == 10.0
    assert view.time_at(0) == pytest.approx(10.0 - view.window_seconds / 2)
    assert view.time_at(800) == pytest.approx(10.0 + view.window_seconds / 2)


def test_zooming_steps_through_the_levels_and_stops_at_the_ends(qapp):
    view = detail()
    assert view.window_seconds == 8.0
    view.zoom(1)
    assert view.window_seconds == ZOOMS[0]
    view.zoom(5)
    assert view.window_seconds == ZOOMS[0]
    view.zoom(-9)
    assert view.window_seconds == ZOOMS[-1]
    view.set_window_seconds(1000)
    assert view.window_seconds == ZOOMS[-1]


def test_the_wheel_zooms_the_detail_view(qapp):
    view = detail(800)
    view.set_position(10.0)

    def wheel(delta):
        return QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )  # fmt: skip

    view.wheelEvent(wheel(120))
    assert view.window_seconds == 4.0
    view.wheelEvent(wheel(-120))
    assert view.window_seconds == 8.0


@pytest.mark.parametrize("preference", ["dark", "light", "system6"])
def test_the_detail_view_draws_in_every_theme_and_says_why_when_empty(qapp, preference):
    try:
        theme.set_theme_preference(preference)
        view = detail()
        view.set_position(10.0)
        assert has_content(pixels(view), view.grab().toImage().pixelColor(0, 0))
        empty = DetailView()
        empty.resize(300, empty.height())
        empty.clear("Loading…")
        pixels(empty)
        view.set_position(0.0)  # the start of the track: nothing before it to draw
        pixels(view)
    finally:
        theme.set_theme_preference("dark")


# -- the track list's preview column --------------------------------------------------------------


def test_return_space_and_command_return_have_their_own_meaning_in_the_track_table(
    qapp,
):
    table = TrackTable()
    seen = []
    table.load_requested.connect(lambda: seen.append("load"))
    table.add_requested.connect(lambda: seen.append("add"))
    table.play_pause_requested.connect(lambda: seen.append("play"))
    QTest.keyClick(table, Qt.Key.Key_Return)
    QTest.keyClick(table, Qt.Key.Key_Enter)  # (the keypad's)
    QTest.keyClick(
        table, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier
    )  # ⌘ on a Mac
    QTest.keyClick(table, Qt.Key.Key_Enter, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(table, Qt.Key.Key_Space)
    QTest.keyClick(table, Qt.Key.Key_A)
    assert seen == ["load", "load", "add", "add", "play"]


def test_a_preview_strip_is_drawn_to_size_and_scaled_up_when_quiet(qapp):
    bands = np.zeros((400, 3), dtype=np.float32)
    bands[:, 0] = 0.1  # a very quiet track: only a little bass
    pixmap = render_preview(Waveform(bands, 0.0), 120, 22)
    assert (pixmap.width(), pixmap.height()) == (120, 22)
    image = pixmap.toImage()
    background = QColor(theme.current_palette()["wave_bg"])
    drawn = sum(image.pixelColor(60, y) != background for y in range(22))
    assert drawn >= 18  # scaled up to use nearly the whole strip
    blank = render_preview(
        Waveform(np.zeros((0, 3), np.float32), 0.0), 50, 20
    ).toImage()
    assert not has_content(blank, background)
    silent = render_preview(
        Waveform(np.zeros((400, 3), np.float32), 0.0), 50, 20
    ).toImage()
    assert not has_content(silent, background)  # silence stays flat


def test_the_model_keeps_previews_and_announces_the_row_that_gained_one(qapp):
    model = TrackTableModel()
    tracks = [make_track(id=i, ext_id=str(i)) for i in (1, 2, 3)]
    model.set_tracks(tracks)
    changed = []
    model.dataChanged.connect(
        lambda top, _bottom: changed.append((top.row(), top.column()))
    )
    assert model.data(
        model.index(1, PREVIEW_COLUMN), PREVIEW_ROLE
    ) is None and not model.has_preview(2)
    wf = sample_waveform()
    model.set_preview(2, wf)
    assert (
        model.has_preview(2)
        and model.data(model.index(1, PREVIEW_COLUMN), PREVIEW_ROLE) is wf
    )
    assert changed == [(1, PREVIEW_COLUMN)]
    model.set_preview(99, wf)  # not on screen: remembered, nothing to redraw
    assert changed == [(1, PREVIEW_COLUMN)]
    assert model.data(model.index(1, PREVIEW_COLUMN)) == ""  # drawn, not written


def test_old_previews_are_forgotten_in_a_huge_library(qapp, monkeypatch):
    from dubcrate.ui import models

    monkeypatch.setattr(models, "MAX_PREVIEWS", 3)
    model = TrackTableModel()
    for i in range(6):
        model.set_preview(i, sample_waveform())
    assert [model.has_preview(i) for i in range(6)] == [
        False,
        False,
        False,
        True,
        True,
        True,
    ]


def test_the_delegate_draws_each_preview_once_and_redraws_after_a_theme_change(
    qapp, monkeypatch
):
    from dubcrate.ui import track_table

    renders = []
    real = track_table.render_preview
    monkeypatch.setattr(
        track_table,
        "render_preview",
        lambda *a, **k: renders.append(1) or real(*a, **k),
    )
    model = TrackTableModel()
    model.set_tracks([make_track(id=5)])
    model.set_preview(5, sample_waveform())
    delegate = PreviewDelegate()
    canvas = QPixmap(200, 40)
    option = QStyleOptionViewItem()
    option.rect = canvas.rect()
    painter = QPainter(canvas)
    index = model.index(0, PREVIEW_COLUMN)
    delegate.paint(painter, option, index)
    delegate.paint(painter, option, index)
    assert len(renders) == 1
    theme.notifier.changed.emit()
    delegate.paint(painter, option, index)
    painter.end()
    assert len(renders) == 2
    assert delegate.sizeHint(option, index).height() >= 20


# -- the preview controller ---------------------------------------------------------------------------


@pytest.fixture
def rig(qapp, tmp_path):
    lib = Library.open(":memory:")
    records = [
        TrackRecord(
            ext_id=n,
            path=write_wav(tmp_path / f"{n}.wav", seconds=s),
            title=n.title(),
            artist="Me",
            duration=s,
        )
        for n, s in (("one", 1.5), ("two", 1.0), ("three", 2.0))
    ]
    records.append(
        TrackRecord(
            ext_id="gone", path=str(tmp_path / "gone.wav"), title="Gone", duration=1.0
        )
    )
    records.append(
        TrackRecord(
            ext_id="bad", path=str(tmp_path / "bad.mp3"), title="Bad", duration=1.0
        )
    )
    (tmp_path / "bad.mp3").write_bytes(b"not audio")
    lib.replace_source("folder", str(tmp_path), "Crate", records)
    strip = PlayerStrip()
    controller = PreviewController(lib, strip, AudioPlayer())
    by_title = {t.title: t for t in lib.tracks()}
    events = SimpleNamespace(messages=[], loaded=[], previews=[])
    controller.message.connect(events.messages.append)
    controller.loaded.connect(events.loaded.append)
    controller.preview_ready.connect(lambda i, w: events.previews.append((i, w)))
    yield SimpleNamespace(
        lib=lib, strip=strip, controller=controller, tracks=by_title, events=events
    )
    controller.shutdown()
    lib.close()


def test_loading_a_track_decodes_it_shows_it_and_starts_it(rig, wait_for):
    rig.controller.load(rig.tracks["One"])
    assert (
        rig.strip.title.text() == "One" and not rig.strip.play_button.isEnabled()
    )  # while it loads
    assert wait_for(lambda: rig.controller.player.playing)
    assert rig.strip.play_button.isEnabled() and rig.controller.track.title == "One"
    assert (
        abs(rig.controller.player.duration - 1.5) < 0.05
        and abs(rig.strip.overview._duration - 1.5) < 0.05
    )
    [(track_id, waveform)] = rig.events.previews
    assert track_id == rig.tracks["One"].id and waveform.columns > 0
    assert [t.title for t in rig.events.loaded] == ["One"]
    assert (
        rig.strip.overview._waveform.columns > 100
    )  # computed: there is no rekordbox analysis for a folder


def test_a_slower_load_that_was_overtaken_is_ignored(rig, wait_for):
    rig.controller.load(rig.tracks["One"])
    rig.controller.load(rig.tracks["Three"])
    assert wait_for(
        lambda: rig.controller.track is not None and rig.controller.player.playing
    )
    assert rig.controller.track.title == "Three"
    wait_for(lambda: False, timeout=0.3)  # let the first worker finish
    assert rig.controller.track.title == "Three" and [
        t.title for t in rig.events.loaded
    ] == ["Three"]
    assert abs(rig.controller.player.duration - 2.0) < 0.05


def test_a_missing_file_is_reported_without_trying_to_decode_it(rig, monkeypatch):
    monkeypatch.setattr(
        preview,
        "run_async",
        lambda *a, **k: pytest.fail("a missing file should not start a worker"),
    )
    rig.controller.load(rig.tracks["Gone"])
    assert (
        rig.strip.detail._message == "File not found"
        and "file not found" in rig.events.messages[0]
    )


def test_a_file_that_cannot_be_decoded_is_reported(rig, wait_for):
    rig.controller.load(rig.tracks["Bad"])
    assert wait_for(lambda: bool(rig.events.messages))
    assert (
        "Couldn't load Bad" in rig.events.messages[0]
        and not rig.controller.player.loaded
    )
    assert not rig.strip.play_button.isEnabled()


def test_choosing_the_loaded_track_again_restarts_it_without_decoding_again(
    rig, wait_for, monkeypatch
):
    rig.controller.load(rig.tracks["One"], play=False)
    assert wait_for(lambda: rig.controller.player.loaded)
    assert not rig.controller.player.playing
    rig.controller.player.seek(1.0)
    monkeypatch.setattr(
        preview, "decode_file", lambda *_: pytest.fail("should not decode again")
    )
    rig.controller.load(rig.tracks["One"])
    assert rig.controller.player.position == 0.0 and rig.controller.player.playing


def test_toggle_only_acts_on_a_loaded_track(rig, wait_for):
    rig.controller.toggle()
    assert not rig.controller.player.playing
    rig.controller.load(rig.tracks["One"])
    assert wait_for(lambda: rig.controller.player.playing)
    rig.controller.toggle()
    assert not rig.controller.player.playing


def test_saved_audio_settings_are_applied_and_the_volume_is_remembered(rig):
    settings.set_audio_device("abc123")
    settings.set_audio_buffer(256)
    settings.set_volume(0.3)
    rig.controller.apply_settings()
    player = rig.controller.player
    assert (player._device_id, player._buffer_frames, player.volume) == (
        "abc123",
        256,
        0.3,
    )
    assert rig.strip.volume.value() == 30
    rig.strip.volume.setValue(55)
    assert settings.get_volume() == 0.55 and player.volume == 0.55


def test_unset_or_nonsense_settings_fall_back_to_defaults(qapp):
    assert (
        settings.get_audio_device(),
        settings.get_audio_buffer(),
        settings.get_volume(),
    ) == (None, 1024, 0.8)
    settings.set_audio_buffer(999)  # not one of the offered sizes
    assert settings.get_audio_buffer() == 1024
    settings.set_audio_device(None)
    assert settings.get_audio_device() is None


def test_rekordbox_analysis_is_used_when_it_exists_and_audio_is_analysed_when_not(
    tmp_path, monkeypatch
):
    path = write_wav(tmp_path / "a.wav", seconds=1.0)
    ready = Analysis(
        waveform=sample_waveform(columns=150), phrases=(Phrase(0, 1, "INTRO"),)
    )
    seen = {}

    def fake_read(root, analysis_path, track_end):
        seen["args"] = (root, analysis_path)
        return ready

    monkeypatch.setattr(preview, "read_analysis", fake_read)
    media = load_media(path, Path("/share"), "/PIONEER/x/ANLZ0000.DAT")
    assert (
        media.waveform is ready.waveform
        and media.analysis is ready
        and seen["args"][0] == Path("/share")
    )
    monkeypatch.setattr(
        preview, "read_analysis", lambda *a: Analysis()
    )  # analysis without a waveform
    assert load_media(path, Path("/share"), "/x").waveform.columns == pytest.approx(
        150, abs=2
    )
    media = load_media(
        path, None, None
    )  # not a rekordbox track: no analysis lookup at all
    assert media.analysis is None and isinstance(media.audio, DecodedAudio)


def test_only_rekordbox_sources_have_an_analysis_folder(qapp, tmp_path):
    lib = Library.open(":memory:")
    lib.replace_source(
        "rekordbox",
        str(tmp_path / "rb" / "master.db"),
        "rb",
        [TrackRecord(ext_id="1", path="/x/1.mp3")],
    )
    lib.replace_source(
        "folder", str(tmp_path), "f", [TrackRecord(ext_id="2", path="/x/2.mp3")]
    )
    controller = PreviewController(lib, PlayerStrip(), AudioPlayer())
    rb, folder = lib.tracks(source_id=1)[0], lib.tracks(source_id=2)[0]
    assert (
        controller._analysis_root(rb) == tmp_path / "rb" / "share"
        and controller._analysis_root(folder) is None
    )
    controller.shutdown()
    lib.close()


# -- the settings dialog --------------------------------------------------------------------------------


@pytest.fixture
def two_devices(monkeypatch):
    found = [{"name": "Studio", "id": "s"}, {"name": "Speakers", "id": "p"}]
    monkeypatch.setattr(devices, "list_output_devices", lambda: found)
    monkeypatch.setattr(
        devices, "device_id_string", lambda d: {"s": "5555", "p": "7777"}[d["id"]]
    )


def test_the_dialog_offers_the_system_default_first_and_the_standard_buffer(
    qapp, two_devices
):
    dialog = SettingsDialog()
    assert [dialog.device_combo.itemText(i) for i in range(3)] == [
        "(System Default)",
        "Studio",
        "Speakers",
    ]
    assert (
        dialog.device_combo.currentIndex() == 0
        and dialog.buffer_combo.currentData() == 1024
    )
    assert (
        "1024 frames" in dialog.buffer_combo.currentText()
        and "23 ms" in dialog.buffer_combo.currentText()
    )


def test_choosing_a_device_and_buffer_saves_them_and_tells_the_app(qapp, two_devices):
    dialog = SettingsDialog()
    changes = []
    dialog.audio_changed.connect(lambda: changes.append(1))
    dialog.device_combo.setCurrentIndex(2)
    dialog.device_combo.activated.emit(2)
    dialog.buffer_combo.setCurrentIndex(dialog.buffer_combo.findData(256))
    dialog.buffer_combo.activated.emit(dialog.buffer_combo.currentIndex())
    assert (settings.get_audio_device(), settings.get_audio_buffer(), len(changes)) == (
        "7777",
        256,
        2,
    )
    again = SettingsDialog()  # a new dialog shows what was saved
    assert (
        again.device_combo.currentText() == "Speakers"
        and again.buffer_combo.currentData() == 256
    )
    dialog.device_combo.setCurrentIndex(0)
    dialog.device_combo.activated.emit(0)
    assert settings.get_audio_device() is None


def test_a_saved_device_that_is_not_connected_is_still_shown(qapp, two_devices):
    settings.set_audio_device("deadbeef")
    dialog = SettingsDialog()
    assert dialog.device_combo.currentText() == "Saved device (not connected)"
    assert dialog.device_combo.currentData() == "deadbeef"


def test_a_machine_with_no_audio_backend_still_opens_the_dialog(qapp, monkeypatch):
    def broken():
        raise RuntimeError("no audio backend")

    monkeypatch.setattr(devices, "list_output_devices", broken)
    assert SettingsDialog().device_combo.count() == 1


def test_the_test_tone_plays_through_the_chosen_settings_and_stops_when_closed(
    qapp, two_devices
):
    dialog = SettingsDialog()
    dialog.buffer_combo.setCurrentIndex(dialog.buffer_combo.findData(512))
    dialog.play_test_tone()
    player = dialog._tone_player
    assert player is not None and player.playing and player._buffer_frames == 512
    device = player._device
    dialog.reject()
    assert dialog._tone_player is None and device.closed


def test_the_test_tone_is_quiet_and_has_soft_edges(qapp):
    tone = make_test_tone()
    assert abs(tone.duration - 0.8) < 0.01 and tone.samples.shape[1] == 2
    assert np.abs(tone.samples).max() <= TONE_LEVEL * 32767 + 1
    assert abs(int(tone.samples[0, 0])) < 100 and abs(int(tone.samples[-1, 0])) < 100


def test_the_theme_choice_is_announced(qapp):
    dialog = SettingsDialog()
    chosen = []
    dialog.theme_chosen.connect(chosen.append)
    dialog.theme_combo.setCurrentIndex(dialog.theme_combo.findData("light"))
    dialog.theme_combo.activated.emit(dialog.theme_combo.currentIndex())
    assert chosen == ["light"]


# -- in the main window -------------------------------------------------------------------------------------


@pytest.fixture
def audible(qapp, tmp_path):
    lib = Library.open(":memory:")
    records = [
        TrackRecord(ext_id=f"t{i}", path=write_wav(tmp_path / f"t{i}.wav", seconds=1.0), title=f"Song {i}", artist="Me",
                    bpm=120 + i, duration=1.0)
        for i in range(4)
    ]  # fmt: skip
    lib.replace_source("folder", str(tmp_path), "Crate", records)
    win = MainWindow(lib)
    win.track_table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
    win._on_sort_changed(
        TRACK_COLUMNS.index(next(c for c in TRACK_COLUMNS if c.title == "Title")),
        Qt.SortOrder.AscendingOrder,
    )
    yield win, lib
    win.close()
    win.deleteLater()
    lib.close()


def row_titles(win):
    return [t.title for t in win.track_model.tracks()]


def test_double_clicking_a_track_previews_it(audible, wait_for):
    win, _ = audible
    win._on_track_double_clicked(win.track_model.index(2, 1))
    assert wait_for(lambda: win.preview.player.playing)
    assert (
        win.preview.track.title == "Song 2"
        and win.player_strip.title.text() == "Song 2"
    )
    assert win.disc_view.model.rowCount() == 0  # previewing is not adding
    assert wait_for(
        lambda: win.track_model.has_preview(win.preview.track.id)
    )  # and it gains a preview strip


def test_space_plays_and_pauses_from_the_track_table(audible, wait_for):
    win, _ = audible
    win._on_track_double_clicked(win.track_model.index(0, 1))
    assert wait_for(lambda: win.preview.player.playing)
    QTest.keyClick(win.track_table, Qt.Key.Key_Space)
    assert not win.preview.player.playing
    QTest.keyClick(win.track_table, Qt.Key.Key_Space)
    assert win.preview.player.playing


def test_previous_and_next_walk_the_track_list(audible, wait_for):
    win, _ = audible
    win._on_track_double_clicked(win.track_model.index(1, 1))
    assert wait_for(lambda: win.preview.player.playing)
    win._step_track(1)
    assert wait_for(lambda: win.preview.track.title == "Song 2")
    assert [i.row() for i in win.track_table.selectionModel().selectedRows()] == [2]
    win._step_track(-1)
    win._step_track(-1)
    assert wait_for(lambda: win.preview.track.title == "Song 0")
    win._step_track(-1)  # already at the top: nothing to do
    assert win.preview.track.title == "Song 0"


def test_next_with_nothing_loaded_starts_from_the_selection(audible, wait_for):
    win, _ = audible
    win.track_table.selectRow(1)
    win._step_track(1)
    assert wait_for(lambda: win.preview.track is not None)
    assert win.preview.track.title == "Song 2"


def test_closing_the_window_releases_the_audio_device(audible, wait_for):
    win, _ = audible
    win._on_track_double_clicked(win.track_model.index(0, 1))
    assert wait_for(lambda: win.preview.player.playing)
    device = win.preview.player._device
    win.close()
    assert device.closed and not win.preview.player.playing


def test_hidden_columns_are_remembered_and_the_title_cannot_be_hidden(audible):
    win, lib = audible
    genre = next(i for i, c in enumerate(TRACK_COLUMNS) if c.title == "Genre")
    win.set_column_visible(genre, False)
    win.set_column_visible(PREVIEW_COLUMN, False)
    assert win.track_table.isColumnHidden(genre)
    assert "Genre" not in settings.get_visible_columns()
    assert "Preview" not in settings.get_visible_columns()
    assert "Title" in settings.get_visible_columns()
    other = MainWindow(lib)
    assert other.track_table.isColumnHidden(genre) and other.track_table.isColumnHidden(
        PREVIEW_COLUMN
    )
    assert not other.track_table.isColumnHidden(1)
    win.set_column_visible(genre, True)
    assert "Genre" in settings.get_visible_columns()
    assert "Preview" not in settings.get_visible_columns()
    other.deleteLater()


def test_previews_are_read_only_for_rows_on_screen_and_only_once(
    qapp, tmp_path, monkeypatch
):
    lib = Library.open(":memory:")
    records = [
        TrackRecord(
            ext_id=str(i),
            path=f"/nowhere/{i}.mp3",
            title=f"T{i}",
            analysis_path=f"/PIONEER/{i}/ANLZ0000.DAT",
        )
        for i in range(300)
    ]
    records.append(
        TrackRecord(ext_id="plain", path="/nowhere/p.mp3", title="No analysis")
    )
    lib.replace_source("rekordbox", str(tmp_path / "master.db"), "rb", records)
    reads = []
    monkeypatch.setattr(
        main_window,
        "read_preview",
        lambda root, p: reads.append((root, p)) or sample_waveform(),
    )
    win = MainWindow(lib)
    win.show()
    win._request_previews()
    assert 0 < len(reads) < 300  # not the whole library
    assert all(root == tmp_path / "share" for root, _ in reads)
    first = len(reads)
    win._request_previews()
    assert len(reads) == first  # already have them
    win.track_table.scrollToBottom()
    win._request_previews()
    assert len(reads) > first
    assert not win.track_model.has_preview(
        next(t.id for t in win.track_model.tracks() if t.title == "No analysis")
    )
    win.close()
    win.deleteLater()
    lib.close()


def test_settings_open_from_the_menu_and_wire_up_to_the_player_and_theme(
    audible, monkeypatch
):
    win, _ = audible
    seen = {}

    def fake_exec(self):
        seen["dialog"] = self
        settings.set_audio_buffer(128)
        self.audio_changed.emit()
        self.theme_chosen.emit("light")
        return 0

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    try:
        win.open_settings()
        assert (
            win.preview.player._buffer_frames == 128
            and theme.current_preference() == "light"
        )
    finally:
        win.set_theme("dark")


# -- the Discs tab ------------------------------------------------------------


def test_the_discs_tab_defaults_to_80_minutes_and_gapless(qapp, two_devices):
    dialog = SettingsDialog()
    assert dialog.minutes_combo.currentData() == 80
    assert dialog.gap_combo.currentData() == 0
    assert dialog.gap_combo.currentText() == "Gapless"
    assert (settings.get_disc_minutes(), settings.get_pregap_seconds()) == (80, 0)


def test_choosing_a_disc_size_and_gap_saves_them(qapp, two_devices):
    dialog = SettingsDialog()
    dialog.minutes_combo.setCurrentIndex(dialog.minutes_combo.findData(74))
    dialog.minutes_combo.activated.emit(dialog.minutes_combo.currentIndex())
    dialog.gap_combo.setCurrentIndex(dialog.gap_combo.findData(2))
    dialog.gap_combo.activated.emit(dialog.gap_combo.currentIndex())
    assert (settings.get_disc_minutes(), settings.get_pregap_seconds()) == (74, 2)
    again = SettingsDialog()
    assert (again.minutes_combo.currentData(), again.gap_combo.currentData()) == (74, 2)


def test_nonsense_saved_disc_settings_fall_back_to_the_defaults(qapp):
    settings._qs().setValue("discs/minutes", 63)
    settings._qs().setValue("discs/pregap_seconds", "lots")
    assert (settings.get_disc_minutes(), settings.get_pregap_seconds()) == (80, 0)


# -- the Library tab ----------------------------------------------------------


def test_updating_the_rekordbox_library_at_startup_is_on_by_default(qapp):
    assert settings.get_auto_import() is True


def test_the_startup_update_can_be_switched_off_and_on_in_settings(qapp):
    dialog = SettingsDialog()
    assert dialog.auto_import_check.isChecked()
    dialog.auto_import_check.setChecked(False)
    assert settings.get_auto_import() is False
    assert not SettingsDialog().auto_import_check.isChecked()  # and it is remembered
    dialog.auto_import_check.setChecked(True)
    assert settings.get_auto_import() is True


def test_nonsense_saved_for_the_startup_update_means_on(qapp):
    settings._qs().setValue("import/auto_rekordbox", "maybe")
    assert settings.get_auto_import() is True
