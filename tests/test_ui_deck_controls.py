"""CUE, loops and quantise in the player: the buttons, the preview doing what the deck decides, and what is shown."""

import wave
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dubcrate.audio.player import AudioPlayer
from dubcrate.library import Library, TrackRecord
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.preview import PreviewController

SECONDS = 30.0
BEATS = np.arange(0.0, SECONDS, 0.5)  # 120 bpm from the very start


def write_wav(path, seconds=SECONDS, rate=44_100):
    t = np.arange(int(rate * seconds)) / rate
    data = (np.sin(2 * np.pi * 220 * t) * 9_000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())
    return str(path)


@pytest.fixture
def rig(qapp, tmp_path, wait_for):
    lib = Library.open(":memory:")
    lib.replace_source(
        "folder",
        str(tmp_path),
        "Crate",
        [
            TrackRecord(
                ext_id=n,
                path=write_wav(tmp_path / f"{n}.wav"),
                title=n,
                artist="Me",
                duration=SECONDS,
            )
            for n in ("a", "b")
        ],
    )
    strip = PlayerStrip()
    strip.resize(1300, 340)
    strip.show()
    controller = PreviewController(lib, strip, AudioPlayer())
    tracks = {t.title: t for t in lib.tracks()}
    messages = []
    controller.message.connect(messages.append)

    def load(name="a", tempo=True):
        controller.load(tracks[name], play=False)
        assert wait_for(lambda: controller.track is not None and controller.track.title == name and controller.player.loaded)  # fmt: skip
        controller.player.pause()
        if tempo:  # (a folder track has no grid: give it one, as rekordbox's would be)
            controller.deck.load(SECONDS, BEATS, 120.0)

    load()
    yield SimpleNamespace(lib=lib, strip=strip, c=controller, player=controller.player, messages=messages, load=load)  # fmt: skip
    controller.shutdown()
    strip.deleteLater()
    lib.close()


def at(rig, seconds, playing=False):
    rig.player.seek(seconds)
    rig.player.play() if playing else rig.player.pause()


def marker_kinds(rig):
    return sorted((c.kind, round(c.time, 2)) for c in rig.strip._markers)


def test_the_new_buttons_wait_for_a_track_and_a_clear_strip_turns_them_off(qapp):
    strip = PlayerStrip()
    buttons = (strip.cue_button, strip.quantise_button, strip.loop_size, strip.loop_size_back, strip.loop_size_forward, strip.loop_in, strip.loop_out, strip.reloop)  # fmt: skip
    assert not any(b.isEnabled() for b in buttons)
    strip._enable(True)
    assert all(b.isEnabled() for b in buttons)
    strip.clear()
    assert not any(b.isEnabled() for b in buttons)
    strip.deleteLater()


def test_stopped_on_the_cue_point_holding_cue_plays_from_it_and_letting_go_returns(rig):
    at(rig, 0.0)
    QTest.mousePress(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.player.playing and rig.player.position < 0.5
    rig.player.seek(3.0)  # (it played on a while)
    QTest.mouseRelease(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert not rig.player.playing and rig.player.position == 0.0


def test_stopped_elsewhere_cue_sets_the_cue_point_and_shows_it(rig):
    at(rig, 7.25)
    QTest.mouseClick(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.c.deck.cue_point == 7.25 and not rig.player.playing
    assert marker_kinds(rig) == [("cue", 7.25)]
    assert any(
        c.kind == "cue" for c in rig.strip.detail._cues
    )  # (drawn on both waveforms)
    assert any(c.kind == "cue" for c in rig.strip.overview._cues)
    assert not rig.strip.side.memory.topLevelItemCount()  # (never in rekordbox's lists)


def test_playing_cue_goes_back_to_the_cue_point_and_stops(rig):
    at(rig, 7.25)
    QTest.mouseClick(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    at(rig, 20.0, playing=True)
    QTest.mouseClick(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert not rig.player.playing and rig.player.position == 7.25


def test_quantise_snaps_the_cue_point_to_a_beat(rig):
    rig.strip.quantise_button.click()
    assert rig.c.deck.quantise
    at(rig, 10.3)
    QTest.mouseClick(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.c.deck.cue_point == pytest.approx(10.5)
    rig.strip.quantise_button.click()
    assert not rig.c.deck.quantise


def test_quantise_on_a_track_with_no_tempo_says_there_is_nothing_to_snap_to(rig):
    rig.load("b", tempo=False)
    rig.strip.quantise_button.click()
    assert "no beat grid or tempo" in rig.messages[-1]


def test_the_size_button_loops_that_many_beats_from_here_and_again_leaves_it(rig):
    at(rig, 4.0)
    assert rig.strip.loop_size.text() == "4"
    rig.strip.loop_size.click()
    assert rig.player.looping and rig.c.deck.loop == (4.0, pytest.approx(6.0))
    assert rig.strip.loop_size.isChecked() and rig.strip.reloop.isChecked()
    assert marker_kinds(rig) == [("loop", 4.0)]
    rig.strip.loop_size.click()
    assert not rig.player.looping and not rig.strip.loop_size.isChecked()
    assert not rig.c.deck.loop_active


def test_the_size_arrows_change_the_size_and_redraw_a_loop_that_is_on(rig):
    labels = []
    for _ in range(3):
        rig.strip.loop_size_forward.click()
        labels.append(rig.strip.loop_size.text())
    assert labels == ["8", "16", "32"]
    rig.strip.loop_size_forward.click()
    assert rig.strip.loop_size.text() == "32"  # (the biggest)
    for _ in range(5):
        rig.strip.loop_size_back.click()
    assert rig.strip.loop_size.text() == "1"
    rig.strip.loop_size_back.click()
    assert rig.strip.loop_size.text() == "1/2"
    rig.strip.loop_size_back.click()
    assert rig.strip.loop_size.text() == "1/2"
    at(rig, 4.0)
    rig.strip.loop_size.click()  # 1/2 beat
    assert rig.c.deck.loop == (4.0, pytest.approx(4.25))
    rig.strip.loop_size_forward.click()  # 1 beat, same start
    assert rig.c.deck.loop == (4.0, pytest.approx(4.5))
    assert rig.player._state.loop_end == round(
        4.5 * 44_100
    )  # (the engine has the new one)


def test_an_auto_loop_with_no_tempo_or_past_the_end_says_why_and_does_nothing(rig):
    rig.load("b", tempo=False)
    at(rig, 4.0)
    rig.strip.loop_size.click()
    assert not rig.player.looping and "manual loop" in rig.messages[-1]
    rig.load("a")
    at(rig, 29.0)
    rig.strip.loop_size.click()  # 4 beats = 2 s: past the end
    assert not rig.player.looping and "past the end" in rig.messages[-1]
    assert not rig.strip.loop_size.isChecked()


def test_manual_in_then_out_loops_and_reloop_leaves_and_comes_back(rig):
    rig.strip.manual_button.click()
    at(rig, 3.0)
    rig.strip.loop_in.click()
    assert not rig.player.looping and marker_kinds(rig) == [("cue", 3.0)]
    at(rig, 5.0)
    rig.strip.loop_out.click()
    assert rig.player.looping and rig.c.deck.loop == (3.0, 5.0)
    assert marker_kinds(rig) == [("loop", 3.0)] and rig.strip.reloop.isChecked()
    rig.strip.reloop.click()  # leave it
    assert not rig.player.looping and not rig.strip.reloop.isChecked()
    assert marker_kinds(rig) == [("cue", 3.0), ("cue", 5.0)]  # (its two edges stay)
    at(rig, 20.0)
    rig.strip.reloop.click()  # back to its start, looping
    assert rig.player.looping and rig.player.position == 3.0 and rig.strip.reloop.isChecked()  # fmt: skip


def test_out_needs_an_in_before_it_and_a_new_in_leaves_the_loop(rig):
    at(rig, 5.0)
    rig.strip.loop_out.click()
    assert not rig.player.looping and rig.messages[-1] == "Press IN first"
    rig.strip.loop_in.click()
    at(rig, 4.0)
    rig.strip.loop_out.click()
    assert not rig.player.looping and rig.messages[-1] == "OUT has to come after IN"
    at(rig, 8.0)
    rig.strip.loop_out.click()
    assert rig.player.looping
    rig.strip.loop_in.click()  # a new IN: the loop that was on is left
    assert not rig.player.looping and not rig.strip.reloop.isChecked()


def test_a_loop_is_session_only_and_loading_another_track_forgets_it_all(rig):
    at(rig, 4.0)
    rig.strip.loop_size.click()
    at(rig, 4.0)
    QTest.mouseClick(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.player.looping
    rig.load("b")
    assert not rig.player.looping and rig.strip._markers == ()
    assert not rig.strip.loop_size.isChecked() and rig.c.deck.loop is None
    assert rig.c.deck.cue_point == 0.0 and not rig.c.deck.cue_set
    assert rig.strip.overview._cues == () and rig.strip.detail._cues == ()


def test_the_loops_audio_really_repeats_in_the_player(rig):
    at(rig, 4.0)
    rig.strip.loop_size.click()  # 4 to 6 s
    rig.player.play()
    state = rig.player._state
    from dubcrate.audio.player import render_block

    for _ in range(int(2.5 * 44_100 / 4096)):  # a little over one pass of the loop
        render_block(state, 4096)
    assert state.frame < round(6.0 * 44_100)  # never carried on past its end
    assert state.frame >= round(4.0 * 44_100)
    assert state.playing and rig.player.looping


def test_the_controls_are_ignored_while_scratching(rig):
    rig.player.begin_scratch()
    rig.strip.loop_size.click()
    assert not rig.player.looping and not rig.c.deck.loop_active
    rig.player.end_scratch()
