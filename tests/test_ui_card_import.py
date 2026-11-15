"""Importing memory cues from the card: the review window, the Sync tab's button, and adding them to tracks."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from dubcrate.audio.player import AudioPlayer
from dubcrate.card_import import DiscFinding, TrackFinding
from dubcrate.library import Library, TrackRecord
from dubcrate.ui import preview, sync_view
from dubcrate.ui.card_import_dialog import CardImportDialog, clock
from dubcrate.ui.player_strip import PlayerStrip
from dubcrate.ui.sync_view import SyncView


def track(n):
    return SimpleNamespace(id=n, path=f"/m/{n}.flac")


def findings():
    return [
        DiscFinding(
            1,
            "D",
            "Folder / D",
            [
                TrackFinding(track(1), 1, "One", "A", [12000, 60500]),
                TrackFinding(track(2), 2, "Two", "", [3000], over_limit=2),
            ],
            loops=1,
            hot_cues=2,
        )
    ]


def test_the_review_lists_every_new_cue_ticked(qapp):
    dialog = CardImportDialog(findings())
    assert [(t.id, times) for t, times in dialog.chosen()] == [
        (1, [12000, 60500]),
        (2, [3000]),
    ]
    assert dialog.import_button.text() == "Import 3 cues"
    assert "2 hot cues" in dialog.notes.text() and "1 loop" in dialog.notes.text()
    disc = dialog.tree.topLevelItem(0)
    assert disc.text(0) == "Folder / D"
    assert disc.child(0).text(0) == "1. One - A"
    assert disc.child(0).child(0).text(1) == "0:12.000"
    assert "2 more cues left out" in disc.child(1).child(1).text(0)


def test_unticking_a_cue_a_track_or_a_disc_leaves_it_out(qapp):
    dialog = CardImportDialog(findings())
    disc = dialog.tree.topLevelItem(0)
    disc.child(0).child(1).setCheckState(0, Qt.CheckState.Unchecked)
    assert [(t.id, times) for t, times in dialog.chosen()] == [
        (1, [12000]),
        (2, [3000]),
    ]
    disc.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert [(t.id, times) for t, times in dialog.chosen()] == [(2, [3000])]
    assert dialog.import_button.text() == "Import 1 cue"
    disc.setCheckState(0, Qt.CheckState.Unchecked)
    assert dialog.chosen() == []
    assert not dialog.import_button.isEnabled()


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
    assert said[-1] == "No new memory cues on the card"


def test_what_is_picked_in_the_review_is_handed_on(view, monkeypatch):
    view._card = (object(), {})
    view._surveys = {1: object()}
    monkeypatch.setattr(sync_view, "survey_new_cues", lambda *a: findings())
    got = []
    view.cues_chosen.connect(got.append)
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: None)
    view.import_from_card()
    assert got == []  # cancelled
    picked = [(track(1), [12000])]
    monkeypatch.setattr(sync_view, "ask_import", lambda _p, found: picked)
    view.import_from_card()
    assert got == [picked]


# -- adding them to tracks ---------------------------------------------------------------------


@pytest.fixture
def controller(qapp, tmp_path):
    lib = Library.open(":memory:")
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id="a", path=str(tmp_path / "a.wav"), title="A", duration=0.5)],
    )  # fmt: skip
    ctl = preview.PreviewController(lib, PlayerStrip(), AudioPlayer())
    ctl.the_track = lib.tracks()[0]
    yield ctl
    ctl.shutdown()
    lib.close()


def test_imported_cues_are_added_to_the_tracks_memory_cues(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(1000, "intro")])
    added = controller.import_memory([(t, [5000, 1020, 9000])])
    assert added == 2  # 1020 is within 50 ms of the cue at 1000
    assert lib.memory_cues(t.path) == [(1000, "intro"), (5000, ""), (9000, "")]


def test_a_track_never_goes_over_ten_memory_cues(controller):
    lib, t = controller._lib, controller.the_track
    lib.set_memory_cues(t.path, [(n * 1000, "") for n in range(1, 9)])
    assert controller.import_memory([(t, [20000, 30000, 40000])]) == 2
    assert len(lib.memory_cues(t.path)) == 10


def test_nothing_new_changes_nothing(controller):
    lib, t = controller._lib, controller.the_track
    assert controller.import_memory([(t, [])]) == 0
    assert lib.memory_cues(t.path) is None


def test_it_waits_while_rekordbox_cues_are_still_being_read(controller, monkeypatch):
    lib, t = controller._lib, controller.the_track
    said = []
    controller.message.connect(said.append)
    monkeypatch.setattr(controller.cues, "cues_for", lambda _t: None)
    assert controller.import_memory([(t, [5000])]) is None
    assert "still being read" in said[0]
    assert lib.memory_cues(t.path) is None
