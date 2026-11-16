"""CUE, loops and quantise in the player: the buttons, the preview doing what the deck decides, and what is shown."""

import wave
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from dubcrate import settings
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
    settings.set_quantise(
        False
    )  # (the player starts as it was left: these tests start with Q off)
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


def test_a_quantised_cue_puts_the_playhead_on_the_beat_and_then_cue_previews_from_it(
    rig,
):
    rig.strip.quantise_button.click()
    at(rig, 10.3)
    QTest.mousePress(
        rig.strip.cue_button, Qt.MouseButton.LeftButton
    )  # sets it on the beat at 10.5
    QTest.mouseRelease(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.c.deck.cue_point == pytest.approx(10.5)
    assert (
        rig.player.position == pytest.approx(10.5, abs=0.01) and not rig.player.playing
    )
    rig.player.seek(10.7)  # a little way off the cue point, on the same beat
    QTest.mousePress(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert rig.player.playing and rig.player.position == pytest.approx(10.5, abs=0.5)
    QTest.mouseRelease(rig.strip.cue_button, Qt.MouseButton.LeftButton)
    assert not rig.player.playing and rig.player.position == pytest.approx(
        10.5, abs=0.01
    )


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


def test_out_jumps_back_to_in_so_the_loop_really_plays_and_does_not_run_on(rig):
    from dubcrate.audio.player import render_block

    at(rig, 3.0, playing=True)
    rig.strip.loop_in.click()
    rig.player.seek(
        5.0
    )  # (the track played on; OUT is pressed with the playhead at the loop's end)
    rig.strip.loop_out.click()
    assert rig.player.looping and rig.player.position == pytest.approx(3.0, abs=0.01)
    state = rig.player._state
    lowest, highest = state.frame, state.frame
    for _ in range(int(7.0 * 44_100 / 2048)):  # three and a half passes of the 2 s loop
        render_block(state, 2048)
        lowest, highest = min(lowest, state.frame), max(highest, state.frame)
    assert lowest >= round(3.0 * 44_100) - 2048 and highest <= round(5.0 * 44_100)
    assert state.playing and rig.player.looping


def test_a_quantised_out_that_rounds_to_before_the_playhead_still_loops(rig):
    rig.strip.quantise_button.click()
    at(rig, 3.0, playing=True)
    rig.strip.loop_in.click()
    rig.player.seek(5.2)  # (the beat nearest is 5.0: behind the playhead)
    rig.strip.loop_out.click()
    assert rig.c.deck.loop == (3.0, pytest.approx(5.0))
    assert rig.player.position == pytest.approx(3.0, abs=0.01) and rig.player.looping


def test_out_while_stopped_also_puts_the_playhead_at_in_ready_to_loop_when_played(rig):
    at(rig, 3.0)
    rig.strip.loop_in.click()
    at(rig, 5.0)  # stopped
    rig.strip.loop_out.click()
    assert rig.player.position == pytest.approx(3.0, abs=0.01)
    assert not rig.player.playing and rig.player.looping


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


# -- memory cues edited in the player ------------------------------------------------------------


def memory_times(rig):
    return [round(rig.strip.side.memory.topLevelItem(i).data(0, 0x0100 + 40), 3) for i in range(rig.strip.side.memory.topLevelItemCount())]  # fmt: skip


def stored(rig):
    return rig.lib.memory_cues(rig.c.track.path)


def reset(rig):
    """Reset, the way it is reached now: the Memory list's right-click menu (on an empty spot)."""
    menu = rig.strip.side.memory_menu(None)
    [action] = [a for a in menu.actions() if a.text() == "Reset to rekordbox's"]
    action.trigger()


def test_the_memory_button_adds_a_memory_cue_at_the_playhead_and_it_is_kept(rig):
    assert (
        rig.strip.memory_button.isEnabled()
        and rig.strip.memory_delete_button.isEnabled()
    )
    at(rig, 12.5)
    rig.strip.memory_button.click()
    assert stored(rig) == [(12500, "")]
    assert memory_times(rig) == [12.5]
    assert rig.strip.side.edited  # (so the list's right-click menu offers Reset)
    assert [c.time for c in rig.strip._cues if c.kind == "memory"] == [
        12.5
    ]  # (and it is drawn on the waveforms)
    at(rig, 3.0)
    rig.strip.memory_button.click()
    assert stored(rig) == [(3000, ""), (12500, "")] and memory_times(rig) == [3.0, 12.5]


def test_a_new_memory_cue_lands_on_the_beat_with_quantise_on(rig):
    rig.strip.quantise_button.click()
    at(rig, 12.3)
    rig.strip.memory_button.click()
    assert stored(rig) == [(12500, "")]


def test_a_memory_cue_already_there_or_one_too_many_is_said_and_not_added(rig):
    at(rig, 12.5)
    rig.strip.memory_button.click()
    rig.strip.memory_button.click()
    assert (
        stored(rig) == [(12500, "")]
        and rig.messages[-1] == "There is a memory cue here already"
    )
    for n in range(2, 11):
        at(rig, float(n * 2))
        rig.strip.memory_button.click()
    assert len(stored(rig)) == 10
    at(rig, 27.0)
    rig.strip.memory_button.click()
    assert len(stored(rig)) == 10 and "10 memory cues" in rig.messages[-1]


def test_the_list_x_deletes_that_cue_and_a_loop_has_none(rig):
    at(rig, 5.0)
    rig.strip.memory_button.click()
    at(rig, 9.0)
    rig.strip.memory_button.click()
    side = rig.strip.side
    side._clicked(side.memory.topLevelItem(0), 1)  # the first row's ✕
    assert stored(rig) == [(9000, "")] and memory_times(rig) == [9.0]
    side._clicked(
        side.memory.topLevelItem(0), 0
    )  # (the time itself jumps there, nothing is deleted)
    assert stored(rig) == [(9000, "")] and rig.player.position == pytest.approx(
        9.0, abs=0.05
    )
    side._clicked(side.memory.topLevelItem(0), 1)
    assert (
        stored(rig) == [] and memory_times(rig) == []
    )  # (all deleted: still an edit, not rekordbox's)
    assert side.edited


def test_the_x_by_memory_deletes_the_one_near_the_playhead_and_says_when_there_is_none(
    rig,
):
    at(rig, 5.0)
    rig.strip.memory_button.click()
    at(rig, 20.0)
    rig.strip.memory_delete_button.click()
    assert (
        stored(rig) == [(5000, "")]
        and rig.messages[-1] == "No memory cue or loop near the playhead"
    )
    at(rig, 5.6)
    rig.strip.memory_delete_button.click()
    assert stored(rig) == []


def test_move_to_playhead_keeps_the_label_and_refuses_a_place_taken(rig):
    rig.lib.set_memory_cues(rig.c.track.path, [(5000, "drop"), (9000, "")])
    rig.c._show_cues()
    side = rig.strip.side
    at(rig, 7.0)
    menu = side.memory_menu(side.memory.topLevelItem(0))
    assert [a.text() for a in menu.actions() if a.text()] == ["Move to Playhead", "Delete", "Reset to rekordbox's"]  # fmt: skip
    next(a for a in menu.actions() if a.text() == "Move to Playhead").trigger()
    assert stored(rig) == [(7000, "drop"), (9000, "")]
    at(rig, 9.01)
    side.memory_menu(side.memory.topLevelItem(0)).actions()[0].trigger()
    assert stored(rig) == [(7000, "drop"), (9000, "")] and "already" in rig.messages[-1]


def test_reset_goes_back_to_rekordboxs_and_forgets_the_edits(rig):
    at(rig, 5.0)
    rig.strip.memory_button.click()
    assert stored(rig) == [(5000, "")]
    reset(rig)
    assert stored(rig) is None and memory_times(rig) == []
    assert not rig.strip.side.edited


def test_a_loop_has_a_delete_but_no_move_and_an_empty_spot_offers_reset_only_when_edited(
    rig,
):
    from dubcrate.analysis import Cue

    side = rig.strip.side
    side.set_cues((Cue("loop", 60.0, end=64.0), Cue("memory", 5.0)))
    loop, plain = side.memory.topLevelItem(1), side.memory.topLevelItem(0)
    assert plain.text(1) == "✕" and loop.text(1) == "✕"  # (rekordbox's loops too)
    assert [a.text() for a in side.memory_menu(loop).actions() if a.text()] == ["Delete"]  # fmt: skip
    assert not side.memory_menu(None).actions()
    side.set_edited(True)
    assert [a.text() for a in side.memory_menu(None).actions()] == [
        "Reset to rekordbox's"
    ]


def test_an_edit_waits_while_rekordboxs_cues_are_still_being_read_so_none_are_lost(
    rig, monkeypatch
):
    monkeypatch.setattr(rig.c.cues, "cues_for", lambda track, announce=True: None)
    at(rig, 5.0)
    rig.strip.memory_button.click()
    assert stored(rig) is None and "still being read" in rig.messages[-1]


def test_hot_cues_and_loops_stay_rekordboxs_whatever_is_edited(rig, monkeypatch):
    from dubcrate.analysis import Cue

    rekordbox = [
        Cue("hot", 20.0, slot=1),
        Cue("memory", 2.0),
        Cue("loop", 30.0, end=32.0),
    ]
    # (rekordbox's own cues; the edits are laid over them by cues_for)
    monkeypatch.setattr(
        rig.c.cues, "_rekordbox_cues", lambda track, announce: rekordbox
    )
    at(rig, 7.0)
    rig.strip.memory_button.click()
    assert stored(rig) == [
        (2000, ""),
        (7000, ""),
    ]  # (it began from rekordbox's own memory cue)
    assert memory_times(rig) == [
        2.0,
        7.0,
        30.0,
    ]  # (the memory loop is listed, and unchanged)
    assert rig.strip._hot_times == {1: 20.0} and rig.strip.hot_buttons[0].isEnabled()


# -- memory loops: MEMORY with a loop on ------------------------------------------------------------


def saved_loops(rig):
    return rig.lib.memory_loops(rig.c.track.path)


def loop_on(rig, start=4.0):
    at(rig, start)
    rig.strip.loop_size.click()  # (an auto loop from here)
    assert rig.c.deck.loop_active
    return rig.c.deck.loop


def test_memory_with_a_loop_on_saves_a_memory_loop_and_the_loop_stays_on(rig):
    start, end = loop_on(rig)
    rig.strip.memory_button.click()
    assert saved_loops(rig) == [(round(start * 1000), round(end * 1000), "")]
    assert (
        stored(rig) is None
    )  # (no memory cue was made, and rekordbox's cues are not an edit)
    assert rig.c.deck.loop_active and rig.player.looping
    assert [(c.kind, c.local) for c in rig.strip._cues if c.kind == "loop"] == [("loop", True)]  # fmt: skip
    assert rig.strip.side.edited
    assert "Dubcrate" in rig.messages[-1]


def test_memory_with_no_loop_on_is_still_a_memory_cue_even_if_a_loop_was_set(rig):
    loop_on(rig)
    rig.strip.loop_size.click()  # (leave it: a loop that is off is a gesture, not something to save)
    at(rig, 12.5)
    rig.strip.memory_button.click()
    assert stored(rig) == [(12500, "")] and saved_loops(rig) == []


def test_the_same_loop_twice_is_said_and_saved_once(rig):
    loop_on(rig)
    rig.strip.memory_button.click()
    rig.strip.memory_button.click()
    assert (
        len(saved_loops(rig)) == 1 and rig.messages[-1] == "This loop is saved already"
    )


def test_a_loop_counts_towards_the_ten_memory_places(rig):
    for n in range(9):
        at(rig, float(n * 2 + 1))
        rig.strip.memory_button.click()
    loop_on(rig, 20.0)
    rig.strip.memory_button.click()
    assert len(saved_loops(rig)) == 1  # (the tenth place)
    rig.strip.loop_size.click()
    at(rig, 27.0)
    rig.strip.memory_button.click()
    assert len(stored(rig)) == 9 and "10 memory cues and loops" in rig.messages[-1]
    loop_on(rig, 24.0)
    rig.strip.memory_button.click()
    assert len(saved_loops(rig)) == 1 and "10 memory cues and loops" in rig.messages[-1]


def test_with_loop_writing_off_a_saved_loop_never_asks_about_or_queues_a_write(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", False)
    calls = []
    monkeypatch.setattr(rig.c.editor, "allow", lambda track: calls.append("allow"))
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: calls.append("saved"))
    loop_on(rig)
    rig.strip.memory_button.click()
    assert saved_loops(rig) and calls == []


def test_a_saved_loop_has_a_row_with_x_but_no_move_and_the_x_deletes_it(rig):
    loop_on(rig)
    rig.strip.memory_button.click()
    side = rig.strip.side
    row = side.memory.topLevelItem(0)
    assert row.text(1) == "✕"
    assert [a.text() for a in side.memory_menu(row).actions() if a.text()] == ["Delete", "Reset to rekordbox's"]  # fmt: skip
    side._clicked(row, 1)
    assert saved_loops(rig) == [] and memory_times(rig) == []


def test_the_x_by_memory_deletes_a_loop_the_playhead_is_inside_or_next_to(rig):
    loop_on(
        rig, 4.0
    )  # 4.0 to 6.0 s at 4 beats of 120 bpm... whatever the size, it is saved
    rig.strip.memory_button.click()
    start, end = saved_loops(rig)[0][:2]
    rig.strip.loop_size.click()  # (leave the loop)
    at(rig, (start + end) / 2000)
    rig.strip.memory_delete_button.click()
    assert saved_loops(rig) == []
    rig.lib.set_memory_loops(rig.c.track.path, [(10_000, 12_000, "")])
    at(rig, 10.4)  # (near its start)
    rig.strip.memory_delete_button.click()
    assert saved_loops(rig) == []
    at(rig, 25.0)
    rig.strip.memory_delete_button.click()
    assert rig.messages[-1] == "No memory cue or loop near the playhead"


def test_reset_forgets_saved_loops_too(rig):
    loop_on(rig)
    rig.strip.memory_button.click()
    reset(rig)
    assert saved_loops(rig) == [] and not rig.strip.side.edited


def test_a_loop_saved_here_is_added_to_rekordboxs_loops_not_in_place_of_them(
    rig, monkeypatch
):
    from dubcrate.analysis import Cue

    rekordbox = [Cue("loop", 20.0, end=22.0), Cue("loop", 25.0, end=26.0, slot=4)]
    monkeypatch.setattr(
        rig.c.cues, "_rekordbox_cues", lambda track, announce: rekordbox
    )
    loop_on(rig, 4.0)
    rig.strip.memory_button.click()
    loops = [c for c in rig.strip._cues if c.kind == "loop"]
    assert sum(c.local for c in loops) == 1 and len(loops) == 3
    assert sorted(c.time for c in loops if not c.local) == [20.0, 25.0]


def test_with_loop_writing_on_a_saved_loop_is_queued_for_rekordbox_and_says_nothing(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    calls = []
    monkeypatch.setattr(rig.c.editor, "allow", lambda track: calls.append("allow") or True)  # fmt: skip
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: calls.append("saved"))
    loop_on(rig)
    rig.messages.clear()
    rig.strip.memory_button.click()
    assert saved_loops(rig) and calls == ["allow", "saved"]
    assert rig.messages == []  # (it is going into rekordbox: no "Dubcrate only" note)


def test_with_loop_writing_on_a_loop_that_cannot_go_to_rekordbox_says_so(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    calls = []
    monkeypatch.setattr(
        rig.c.editor, "allow", lambda track: False
    )  # (not a rekordbox track, or declined)
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: calls.append("saved"))
    loop_on(rig)
    rig.strip.memory_button.click()
    assert saved_loops(rig) and calls == []
    assert rig.messages[-1] == "Loop saved in Dubcrate (not in rekordbox)"


# -- quantise is on the first time and then as it was left ------------------------------------------


def make_controller(qapp):
    strip = PlayerStrip()
    controller = PreviewController(Library.open(":memory:"), strip, AudioPlayer())
    return strip, controller


def test_quantise_is_on_by_default_the_first_time(qapp):
    from PySide6.QtCore import QSettings

    QSettings().remove("playback/quantise")
    assert settings.get_quantise() is True
    strip, controller = make_controller(qapp)
    assert strip.quantise_button.isChecked() and controller.deck.quantise
    controller.shutdown()


def test_the_last_quantise_choice_is_remembered_for_the_next_start(qapp):
    strip, controller = make_controller(qapp)
    strip.quantise_button.setChecked(False)
    assert settings.get_quantise() is False
    controller.shutdown()
    strip, controller = make_controller(qapp)  # (a new window, as after a restart)
    assert not strip.quantise_button.isChecked() and not controller.deck.quantise
    strip.quantise_button.setChecked(True)
    assert settings.get_quantise() is True
    controller.shutdown()
    strip, controller = make_controller(qapp)
    assert strip.quantise_button.isChecked() and controller.deck.quantise
    controller.shutdown()


# -- deleting a memory loop that rekordbox has -------------------------------------------------------


def rekordbox_loops(rig, monkeypatch, *loops):
    """rekordbox's own cues for the loaded track: memory loops (start, end) and a hot cue's loop."""
    from dubcrate.analysis import Cue

    cues = [Cue("loop", s, end=e) for s, e in loops] + [
        Cue("loop", 25.0, end=26.0, slot=4)
    ]
    monkeypatch.setattr(rig.c.cues, "_rekordbox_cues", lambda track, announce: cues)
    rig.c._show_cues()


def test_deleting_a_loop_rekordbox_has_hides_it_and_notes_it_for_deletion_there(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    saved = []
    monkeypatch.setattr(rig.c.editor, "allow", lambda track: True)
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: saved.append(track))
    rekordbox_loops(rig, monkeypatch, (20.0, 22.0), (10.0, 12.0))
    side = rig.strip.side
    assert [side.memory.topLevelItem(i).text(1) for i in range(2)] == ["✕", "✕"]
    side._clicked(side.memory.topLevelItem(0), 1)  # the first row: the loop at 10 s
    assert rig.lib.memory_loops_removed(rig.c.track.path) == [(10000, 12000)]
    assert memory_times(rig) == [20.0]  # (hidden from the list at once)
    assert saved == [rig.c.track] and rig.messages == []
    assert side.edited  # (so the list's right-click menu offers Reset)
    assert [(c.kind, c.slot) for c in rig.strip._cues if c.kind == "loop"] == [
        ("loop", None),
        ("loop", 4),  # (the hot cue's loop is not touched)
    ]


def test_with_loop_writing_off_a_deleted_rekordbox_loop_says_it_stays_in_dubcrate(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", False)
    rekordbox_loops(rig, monkeypatch, (10.0, 12.0))
    side = rig.strip.side
    side._clicked(side.memory.topLevelItem(0), 1)
    assert rig.lib.memory_loops_removed(rig.c.track.path) == [(10000, 12000)]
    assert rig.messages[-1] == "Loop deleted in Dubcrate (not in rekordbox)"


def test_deleting_a_loop_saved_here_that_rekordbox_lacks_only_drops_it(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    saved = []
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: saved.append(track))
    loop_on(rig)
    rig.strip.memory_button.click()
    assert saved_loops(rig)
    side = rig.strip.side
    side._clicked(side.memory.topLevelItem(0), 1)
    assert saved_loops(rig) == [] and rig.lib.memory_loops_removed(rig.c.track.path) == []  # fmt: skip
    assert saved == []  # (rekordbox never had it: nothing to write)


def test_a_loop_written_to_rekordbox_but_still_saved_here_is_deleted_in_both(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    monkeypatch.setattr(rig.c.editor, "allow", lambda track: True)
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: None)
    rekordbox_loops(rig, monkeypatch, (10.0, 12.0))
    rig.lib.set_memory_loops(
        rig.c.track.path, [(10010, 11990, "")]
    )  # (not yet dropped)
    rig.c._show_cues()
    assert memory_times(rig) == [10.0]  # (shown once)
    side = rig.strip.side
    side._clicked(side.memory.topLevelItem(0), 1)
    assert rig.lib.memory_loops(rig.c.track.path) == []
    assert rig.lib.memory_loops_removed(rig.c.track.path) == [(10000, 12000)]


def test_the_x_by_memory_deletes_a_rekordbox_loop_the_playhead_is_in(rig, monkeypatch):  # fmt: skip
    from dubcrate.ui.rekordbox_editor import RekordboxEditor

    monkeypatch.setattr(RekordboxEditor, "WRITE_LOOPS", True)
    monkeypatch.setattr(rig.c.editor, "allow", lambda track: True)
    monkeypatch.setattr(rig.c.editor, "saved", lambda track: None)
    rekordbox_loops(rig, monkeypatch, (10.0, 14.0))
    at(rig, 12.0)
    rig.strip.memory_delete_button.click()
    assert rig.lib.memory_loops_removed(rig.c.track.path) == [(10000, 14000)]


def test_reset_forgets_deleted_loops_too(rig, monkeypatch):
    rekordbox_loops(rig, monkeypatch, (10.0, 12.0))
    side = rig.strip.side
    side._clicked(side.memory.topLevelItem(0), 1)
    assert rig.lib.memory_loops_removed(rig.c.track.path)
    reset(rig)
    assert rig.lib.memory_loops_removed(rig.c.track.path) == [] and memory_times(rig) == [10.0]  # fmt: skip
    assert not rig.strip.side.edited
