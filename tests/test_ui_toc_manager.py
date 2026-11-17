"""The TOC Manager window: its list, the tick boxes, reading a disc (the drive stood in for)."""

import pytest
from PySide6.QtCore import Qt

from dubcrate import cdj_card as cc
from dubcrate.card_report import msf
from dubcrate.drive_toc import DriveTocError
from dubcrate.library import Library, TrackRecord
from dubcrate.ui.toc_manager_dialog import (
    COL_DIFFERENCE,
    COL_TOC,
    TocManagerDialog,
    difference_text,
)

LENGTHS = [20000, 25000, 30000, 15000]
PLAN = cc.toc_from_lengths(LENGTHS)


def late(by=1):
    starts, lead_out = PLAN
    return [starts[0], *(s + by for s in starts[1:])], lead_out + by


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def make_disc(lib, tmp_path, measured=True):
    for n in range(len(LENGTHS)):
        (tmp_path / f"{n}.flac").write_bytes(b"x")
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=str(n), path=str(tmp_path / f"{n}.flac"), title=str(n), duration=n + 1.0) for n in range(len(LENGTHS))],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")]
    if measured:
        for track_id, sectors in zip(ids, LENGTHS, strict=True):
            lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc("Banger")
    lib.add_to_disc(disc_id, ids)
    return disc_id


def dialog_for(lib, disc_id, read=None):
    dialog = TocManagerDialog(lib, disc_id, None, read_toc=read)
    return dialog


def read_it(dialog, wait_for):
    dialog.read_button.click()
    assert wait_for(lambda: dialog.read_button.isEnabled())


def test_the_list_shows_each_known_toc_ticked_with_how_it_differs(lib, tmp_path):
    disc_id = make_disc(lib, tmp_path)
    lib.add_disc_toc(disc_id, PLAN, late())
    dialog = dialog_for(lib, disc_id)
    assert dialog.tree.topLevelItemCount() == 2
    item = dialog.tree.topLevelItem(1)
    assert item.checkState(COL_TOC) == Qt.CheckState.Checked
    assert "1 frame longer" in item.text(COL_DIFFERENCE)
    assert "1 ticked TOC" in dialog.plan_label.text()


def test_unticking_a_toc_keeps_it_but_sync_ignores_it(lib, tmp_path, qapp):
    disc_id = make_disc(lib, tmp_path)
    lib.add_disc_toc(disc_id, PLAN, late())
    dialog = dialog_for(lib, disc_id)
    changes = []
    dialog.changed.connect(lambda: changes.append(1))
    dialog.tree.topLevelItem(1).setCheckState(COL_TOC, Qt.CheckState.Unchecked)
    [known] = lib.disc_tocs(disc_id)
    assert known.enabled is False and changes == [1]
    assert dialog.tree.topLevelItemCount() == 2  # (still listed, after the plan's row)
    assert "uses the plan's" in dialog.plan_label.text()
    dialog.tree.topLevelItem(1).setCheckState(COL_TOC, Qt.CheckState.Checked)
    assert lib.disc_tocs(disc_id)[0].enabled is True


def test_a_toc_for_a_playlist_edited_since_is_listed_unusable_and_can_be_removed(
    lib, tmp_path
):
    disc_id = make_disc(lib, tmp_path)
    lib.add_disc_toc(disc_id, ([150, 1, 2, 3], 4), late())  # made for another plan
    dialog = dialog_for(lib, disc_id)
    item = dialog.tree.topLevelItem(1)
    assert "edited since" in item.text(COL_DIFFERENCE)
    assert not item.flags() & Qt.ItemFlag.ItemIsUserCheckable
    dialog.tree.setCurrentItem(item)
    assert dialog.remove_button.isEnabled()
    dialog.remove_button.click()
    assert lib.disc_tocs(disc_id) == []
    assert dialog.tree.topLevelItemCount() == 1  # (the plan's row stays)


def test_reading_a_plausible_disc_records_it(lib, tmp_path, wait_for):
    disc_id = make_disc(lib, tmp_path)
    dialog = dialog_for(lib, disc_id, read=lambda: late())
    changes = []
    dialog.changed.connect(lambda: changes.append(1))
    read_it(dialog, wait_for)
    [known] = lib.disc_tocs(disc_id)
    assert known.source == "manual" and known.actual == late()
    assert "Recorded" in dialog.status.text() and changes == [1]
    assert dialog.tree.topLevelItemCount() == 2
    read_it(dialog, wait_for)  # the same disc again
    assert len(lib.disc_tocs(disc_id)) == 1 and "already" in dialog.status.text()


def test_reading_another_disc_records_nothing_and_says_why(lib, tmp_path, wait_for):
    disc_id = make_disc(lib, tmp_path)
    other = cc.toc_from_lengths([5000, 5000, 5000, 5000])
    dialog = dialog_for(lib, disc_id, read=lambda: other)
    read_it(dialog, wait_for)
    assert lib.disc_tocs(disc_id) == []
    assert "too different" in dialog.status.text()


def test_a_drive_with_no_disc_says_so_in_plain_words(lib, tmp_path, wait_for):
    disc_id = make_disc(lib, tmp_path)

    def read():
        raise DriveTocError("There's no readable disc in the drive (No media).")

    dialog = dialog_for(lib, disc_id, read=read)
    read_it(dialog, wait_for)
    assert dialog.status.text() == "There's no readable disc in the drive (No media)."
    assert lib.disc_tocs(disc_id) == []


def test_tracks_without_a_measured_length_are_measured_before_the_disc_is_read(
    lib, tmp_path, wait_for, monkeypatch
):
    disc_id = make_disc(lib, tmp_path, measured=False)
    ids = [e.track.id for e in lib.disc_entries(disc_id)]
    monkeypatch.setattr(
        "dubcrate.ui.toc_manager_dialog.probe_lengths",
        lambda tracks, *a, **k: (dict(zip(ids, LENGTHS, strict=True)), []),
    )
    dialog = dialog_for(lib, disc_id, read=lambda: late())
    assert "no measured length" in dialog.plan_label.text()
    read_it(dialog, wait_for)
    assert [k.actual for k in lib.disc_tocs(disc_id)] == [late()], dialog.status.text()


def test_the_difference_is_said_in_a_few_words():
    assert difference_text(PLAN, PLAN) == "Same as planned"
    assert difference_text(PLAN, late(150)) == "150 frames (2.0 s) longer than planned"
    shorter = ([150, 20149, 45149, 75149], 90149)
    assert "1 frame shorter" in difference_text(PLAN, shorter)


def test_the_plans_own_toc_is_the_first_row_greyed_unticked_and_cant_be_removed(
    lib, tmp_path
):
    disc_id = make_disc(lib, tmp_path)
    dialog = dialog_for(lib, disc_id)
    plan = dialog.tree.topLevelItem(0)
    assert plan.text(COL_TOC) == "4 tracks, " + msf(PLAN[1])
    assert plan.text(1) == "As planned" and "In use" in plan.text(COL_DIFFERENCE)
    assert not plan.flags() & Qt.ItemFlag.ItemIsUserCheckable
    dialog.tree.setCurrentItem(plan)
    assert not dialog.remove_button.isEnabled()
    lib.add_disc_toc(disc_id, PLAN, late())
    dialog.fill()
    assert "Used when no TOC below is ticked" in dialog.tree.topLevelItem(0).text(
        COL_DIFFERENCE
    )


def test_with_unmeasured_tracks_there_is_no_plan_row_to_show(lib, tmp_path):
    dialog = dialog_for(lib, make_disc(lib, tmp_path, measured=False))
    assert dialog.tree.topLevelItemCount() == 0
