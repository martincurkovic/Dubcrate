"""Writing a sync onto a card: planned fresh, the card's files copied aside first (a folder is the card)."""

from pathlib import Path

import pytest

from dubcrate import card_apply
from dubcrate import cdj_card as cc
from dubcrate.card_sync import DiscRequest, WantedCue

DATA = Path(__file__).parent / "data" / "cdj_card"


@pytest.fixture
def card(tmp_path):
    """A card with three discs in its index, CD 1 with its file (the player's own)."""
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes((DATA / "index_three_discs.PDJ").read_bytes())
    (folder / "CDJ00001.PDJ").write_bytes((DATA / "disc1_full.PDJ").read_bytes())
    return tmp_path / "card"


def files_of(card):
    folder = card / "PIONEER" / "CDJ"
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def cd2_request(*cues):
    starts, lead_out = cc.toc_from_drutil(
        (DATA / "CD2_TOC.txt").read_text(encoding="utf-8")
    )
    return DiscRequest(cc.DiscId.from_toc(starts, lead_out), list(cues))


def test_a_sync_copies_the_card_aside_then_writes_and_says_what_it_did(card, tmp_path):
    before = files_of(card)
    out = card_apply.apply_sync(
        card, [cd2_request(WantedCue(1, 1000), WantedCue(2, 90_000))], tmp_path / "bk"
    )
    assert out.wrote and out.written == ["CDJ00002.PDJ"]
    assert {p.name: p.read_bytes() for p in out.backup.iterdir()} == before
    _, files = cc.read_card(card)
    assert [(c.track, c.position) for c in files["CDJ00002"].cues] == [
        (1, 1000),
        (2, 90_000),
    ]
    assert (
        files_of(card)["CDJ00001.PDJ"] == before["CDJ00001.PDJ"]
    )  # the player's own file


def test_a_second_sync_has_nothing_to_write_and_makes_no_backup(card, tmp_path):
    request = cd2_request(WantedCue(1, 1000))
    card_apply.apply_sync(card, [request], tmp_path / "bk")
    again = card_apply.apply_sync(card, [request], tmp_path / "bk")
    assert not again.wrote and again.backup is None
    assert len(list((tmp_path / "bk").iterdir())) == 1


def test_a_disc_with_no_cues_writes_nothing(card, tmp_path):
    out = card_apply.apply_sync(card, [cd2_request()], tmp_path / "bk")
    assert not out.wrote and not (tmp_path / "bk").exists()


def test_the_card_is_planned_when_written_not_when_the_list_was_made(card, tmp_path):
    first = cd2_request(WantedCue(1, 1000))
    card_apply.apply_sync(card, [first], tmp_path / "bk")  # (another sync, meanwhile)
    out = card_apply.apply_sync(
        card, [cd2_request(WantedCue(1, 1000), WantedCue(1, 2000))], tmp_path / "bk"
    )
    assert [(c.track, c.position) for c in out.plan.discs[0].added] == [(1, 2000)]


def test_a_failed_write_leaves_the_backup_and_says_so_plainly(
    card, tmp_path, monkeypatch
):
    before = files_of(card)

    def full(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cc, "write_card", full)
    with pytest.raises(
        cc.CardFormatError, match="couldn't be read or written.*No space"
    ):
        card_apply.apply_sync(card, [cd2_request(WantedCue(1, 1000))], tmp_path / "bk")
    (backup,) = list((tmp_path / "bk").iterdir())  # copied before the write was tried
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before
    assert files_of(card) == before


def test_a_folder_that_is_not_a_card_is_a_plain_error_and_nothing_is_made(tmp_path):
    with pytest.raises(cc.CardFormatError, match="is this the player's card"):
        card_apply.apply_sync(
            tmp_path, [cd2_request(WantedCue(1, 1000))], tmp_path / "bk"
        )
    assert not (tmp_path / "bk").exists()


def test_the_default_backup_folder_is_next_to_the_library():
    assert card_apply.default_backup_dir().name == "card-backups"
