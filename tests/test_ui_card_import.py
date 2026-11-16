"""Importing changes from the card: the review window, the Sync tab's button, and applying them to tracks."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from dubcrate.audio.player import AudioPlayer
from dubcrate.card_import import (
    ADD,
    HOT,
    LOOP,
    MOVE,
    MOVE_LOOP,
    REMOVE,
    REMOVE_LOOP,
    DiscFinding,
    ImportChoice,
    Row,
    TrackFinding,
)
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import preview, sync_view
from dubcrate.ui.card_import_dialog import CardImportDialog, clock, describe
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.sync_view import SyncView


def track(n):
    return SimpleNamespace(id=n, path=f"/m/{n}.flac")


def findings(baseline=True):
    return [
        DiscFinding(
            1,
            "D",
            "Folder / D",
            [
                TrackFinding(
                    track(1),
                    1,
                    "One",
                    "A",
                    [
                        Row(ADD, 12000),
                        Row(LOOP, 40000, 44000),
                        Row(REMOVE, 3000),
                        Row(HOT, 60500, slot=2, ticked=False),
                    ],
                ),
                TrackFinding(
                    track(2),
                    2,
                    "Two",
                    "",
                    [Row(MOVE, 9000, old_ms=5000)],
                    over_limit=2,
                ),
            ],
            kept=1,
            has_baseline=baseline,
        )
    ]


def test_the_review_lists_every_change_and_ticks_all_but_the_hot_cues(qapp):
    dialog = CardImportDialog(findings())
    chosen = dialog.chosen()
    assert [c.track.id for c in chosen] == [1, 2]
    one, two = chosen
    assert one.add == [12000] and one.loops == [(40000, 44000)] and one.remove == [3000]
    assert (two.add, two.remove) == (
        [9000],
        [5000],
    )  # (a move is a deletion and an addition)
    assert dialog.import_button.text() == "Import 4 changes"
    disc = dialog.tree.topLevelItem(0)
    assert disc.text(0) == "Folder / D" and disc.checkState(0) == Qt.CheckState.PartiallyChecked  # fmt: skip
    assert disc.child(0).text(0) == "1. One - A"
    assert (
        disc.child(0).checkState(0) == Qt.CheckState.PartiallyChecked
    )  # (its hot cue is not)
    assert disc.child(1).checkState(0) == Qt.CheckState.Checked
    assert "2 more changes left out" in disc.child(1).child(1).text(0)


def test_the_words_and_times_of_each_kind_of_change():
    assert describe(Row(ADD, 12000)) == ("New memory cue", "0:12.000")
    assert describe(Row(LOOP, 40000, 44000)) == ("New loop", "0:40.000 → 0:44.000")
    assert describe(Row(HOT, 9000, slot=2, ticked=False))[0] == "Hot cue B on the player, as a memory cue"  # fmt: skip
    assert describe(Row(REMOVE, 3000)) == ("Deleted on the player", "0:03.000")
    assert describe(Row(REMOVE_LOOP, 6000, 7000))[0] == "Loop deleted on the player"
    assert describe(Row(MOVE, 9000, old_ms=5000)) == ("Moved on the player", "0:05.000 → 0:09.000")  # fmt: skip
    words, when = describe(Row(MOVE_LOOP, 12000, 13000, 10000, 11000))
    assert words == "Loop moved on the player"
    assert when == "0:10.000–0:11.000  →  0:12.000–0:13.000"


def test_a_hot_cue_can_be_ticked_to_opt_in(qapp):
    dialog = CardImportDialog(findings())
    hot = dialog.tree.topLevelItem(0).child(0).child(3)
    assert hot.checkState(0) == Qt.CheckState.Unchecked
    hot.setCheckState(0, Qt.CheckState.Checked)
    one = dialog.chosen()[0]
    assert one.add == [12000, 60500]
    assert dialog.tree.topLevelItem(0).child(0).checkState(0) == Qt.CheckState.Checked
    assert dialog.import_button.text() == "Import 5 changes"


def test_unticking_a_change_a_track_or_a_disc_leaves_it_out(qapp):
    dialog = CardImportDialog(findings())
    disc = dialog.tree.topLevelItem(0)
    disc.child(0).child(1).setCheckState(0, Qt.CheckState.Unchecked)  # the loop
    one = dialog.chosen()[0]
    assert one.add == [12000] and one.loops == [] and one.remove == [3000]
    disc.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert [c.track.id for c in dialog.chosen()] == [2]
    assert dialog.import_button.text() == "Import 1 change"
    disc.setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog.chosen() == []
    assert not dialog.import_button.isEnabled()


def test_the_notes_say_what_cannot_be_deleted_and_when_deletions_are_noticed(qapp):
    dialog = CardImportDialog(findings(baseline=True))
    assert "1 cue deleted on the player can't be deleted here" in dialog.notes.text()
    assert "next sync" not in dialog.notes.text()
    plain = CardImportDialog(findings(baseline=False))
    assert "Deletions and moves are noticed from the next sync on" in plain.notes.text()


def test_the_clock():
    assert clock(0) == "0:00.000" and clock(61500) == "1:01.500"


# -- the Sync tab's button -------------------------------------------------------------------


@pytest.fixture
def view(qapp):
    with Library.open(":memory:") as lib:
        yield SyncView(lib)


def test_the_button_needs_a_card_and_says_when_nothing_is_new(view, monkeypatch):
    said = []
    view.message.connect(said.append)
    assert not view.import_button.isEnabled()
    view.import_from_card()
    assert said == ["Choose a card first"]
    view._card = (object(), {})
    view._surveys = {1: SimpleNamespace(writable=False, to_add=0, waves_to_add=0)}
    view._update_buttons()
    assert view.import_button.isEnabled()
    monkeypatch.setattr(sync_view, "survey_new_cues", lambda *a: [])
    view.import_from_card()
    assert said[-1] == "Nothing new on the card"


def test_what_is_picked_in_the_review_is_handed_on(view, monkeypatch):
    view._card = (object(), {})
    view._surveys = {1: object()}
    monkeypatch.setattr(sync_view, "survey_new_cues", lambda *a: findings())
    got = []
    view.cues_chosen.connect(got.append)
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: None)
    view.import_from_card()
    assert got == []  # cancelled
    picked = [ImportChoice(track(1), add=[12000])]
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: picked)
    view.import_from_card()
    assert got == [picked]


# -- applying them to tracks -------------------------------------------------------------------


@pytest.fixture
def controller(qapp, tmp_path):
    lib = Library.open(":memory:")
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id="a", path=str(tmp_path / "a.wav"), title="A", duration=0.5)],
    )  # fmt: skip
    ctl = preview.PreviewController(lib, PlayerStrip(), AudioPlayer())
    ctl.the_track = lib.tracks()[0]
    ctl.saved_calls = []
    ctl.editor.saved = lambda t: ctl.saved_calls.append(
        t
    )  # (nothing is written to rekordbox here)
    yield ctl
    ctl.shutdown()
    lib.close()


def test_imported_cues_are_added_to_the_tracks_memory_cues(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(1000, "intro")])
    result = controller.import_memory([ImportChoice(t, add=[5000, 1020, 9000])])
    assert result.added == 2  # 1020 is within 50 ms of the cue at 1000
    assert lib.memory_cues(t.path) == [(1000, "intro"), (5000, ""), (9000, "")]
    assert controller.saved_calls == [t]  # (queued for rekordbox if that is allowed)


def test_imported_loops_are_saved_in_dubcrate_and_a_known_loop_is_not_added_twice(
    controller,
):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_loops(t.path, [(20000, 24000, "")])
    result = controller.import_memory([ImportChoice(t, loops=[(20010, 23990), (30000, 34000)])])  # fmt: skip
    assert (result.loops, result.added) == (1, 0)
    assert lib.memory_loops(t.path) == [(20000, 24000, ""), (30000, 34000, "")]


def test_a_cue_deleted_on_the_player_is_deleted_from_the_memory_cues(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(1000, "a"), (5000, "b"), (9000, "")])
    result = controller.import_memory([ImportChoice(t, remove=[5000])])
    assert result.removed == 1 and result.added == 0
    assert lib.memory_cues(t.path) == [(1000, "a"), (9000, "")]


def test_a_loop_saved_in_dubcrate_and_deleted_on_the_player_is_deleted(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_loops(t.path, [(20000, 24000, ""), (30000, 34000, "")])
    result = controller.import_memory([ImportChoice(t, remove_loops=[(20000, 24000)])])
    assert result.removed == 1
    assert lib.memory_loops(t.path) == [(30000, 34000, "")]
    assert lib.memory_loops_removed(t.path) == []  # (rekordbox never had it)


def test_a_loop_rekordbox_has_and_the_player_deleted_is_hidden_and_noted_for_rekordbox(controller, monkeypatch):  # fmt: skip
    from dubcrate.analysis import Cue

    lib, t = controller._lib, controller.the_track
    cues = [Cue("loop", 20.0, end=24.0), Cue("loop", 50.0, end=52.0, slot=4)]
    monkeypatch.setattr(
        controller.cues, "_rekordbox_cues", lambda track, announce: cues
    )
    result = controller.import_memory([ImportChoice(t, remove_loops=[(20000, 24000), (50000, 52000)])])  # fmt: skip
    assert (
        result.removed == 1
    )  # (the hot cue's loop is not a memory loop: never deleted)
    assert lib.memory_loops_removed(t.path) == [(20000, 24000)]
    assert controller.saved_calls == [t]


def test_a_move_is_a_deletion_and_an_addition(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(1000, ""), (5000, "")])
    result = controller.import_memory([ImportChoice(t, add=[7000], remove=[5000])])
    assert (result.added, result.removed) == (1, 1)
    assert lib.memory_cues(t.path) == [(1000, ""), (7000, "")]


def test_a_move_by_a_few_milliseconds_is_not_taken_for_a_duplicate_of_the_old_cue(
    controller,
):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(5000, "")])
    controller.import_memory([ImportChoice(t, add=[5020], remove=[5000])])
    assert lib.memory_cues(t.path) == [(5020, "")]


def test_a_track_never_goes_over_ten_memory_cues_and_loops(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(n * 1000, "") for n in range(1, 8)])  # seven
    lib.set_memory_loops(t.path, [(70000, 72000, "")])  # eight places
    result = controller.import_memory(
        [ImportChoice(t, add=[20000, 30000], loops=[(40000, 44000), (50000, 54000)])]
    )
    assert (result.added, result.loops, result.left_out) == (2, 0, 2)
    assert len(lib.memory_cues(t.path)) == 9 and len(lib.memory_loops(t.path)) == 1


def test_a_deletion_makes_room_for_an_addition_in_a_full_track(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(n * 1000, "") for n in range(1, 11)])  # ten
    result = controller.import_memory([ImportChoice(t, add=[50500], remove=[3000])])
    assert (result.added, result.removed, result.left_out) == (1, 1, 0)
    assert len(lib.memory_cues(t.path)) == 10


def test_nothing_new_changes_nothing(controller):
    lib, t = controller._lib, controller.the_track
    result = controller.import_memory([ImportChoice(t)])
    assert result.changed == 0
    assert lib.memory_cues(t.path) is None and controller.saved_calls == []


def test_a_deletion_of_a_cue_that_is_already_gone_does_nothing(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(1000, "")])
    result = controller.import_memory([ImportChoice(t, remove=[5000])])
    assert result.removed == 0 and lib.memory_cues(t.path) == [(1000, "")]
    assert controller.saved_calls == []


def test_it_waits_while_rekordbox_cues_are_still_being_read(controller, monkeypatch):
    lib, t = controller._lib, controller.the_track
    said = []
    controller.message.connect(said.append)
    monkeypatch.setattr(controller.cues, "cues_for", lambda _t: None)
    assert controller.import_memory([ImportChoice(t, add=[5000])]) is None
    assert "still being read" in said[0]
    assert lib.memory_cues(t.path) is None
