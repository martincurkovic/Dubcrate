"""`dubcrate card show | backup | sync` (a temporary folder is the card; no ffmpeg, no drive)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, cli
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


@pytest.fixture
def card(tmp_path):
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes((DATA / "index_three_discs.PDJ").read_bytes())
    (folder / "CDJ00001.PDJ").write_bytes((DATA / "disc1_full.PDJ").read_bytes())
    return tmp_path / "card"


@pytest.fixture
def db(tmp_path):
    """A library file with disc 1 = CD 2's track lengths, and disc 2 = a single track."""
    starts, lead_out = cc.toc_from_drutil(
        (DATA / "CD2_TOC.txt").read_text(encoding="utf-8")
    )
    lengths = [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]
    path = tmp_path / "lib.db"
    with Library.open(path) as lib:
        lib.replace_source(
            "folder", "/m", "M",
            [TrackRecord(ext_id=f"t{n}", path=f"/nonexistent/t{n}.flac", title=f"t{n}", duration=60.0) for n in range(13)],
        )  # fmt: skip
        by_title = {t.title: t for t in lib.tracks()}
        ordered = [by_title[f"t{n}"].id for n in range(13)]
        for track_id, sectors in zip(ordered, [*lengths, 5000], strict=True):
            lib.set_cd_sectors(track_id, sectors)
        full = lib.create_disc("Full")
        lib.add_to_disc(full, ordered[:12])
        lib.mark_burned(full)
        single = lib.create_disc("Single")
        lib.add_to_disc(single, ordered[12:])
    return path


@pytest.fixture(autouse=True)
def cues(monkeypatch):
    """rekordbox cues for the first and last track of the 12-track disc."""
    wanted = {
        "t0": [Cue("memory", 15.0), Cue("hot", 40.0, slot=1)],
        "t11": [Cue("loop", 30.0, end=34.0)],
    }
    monkeypatch.setattr(
        card_discs, "rekordbox_cues", lambda _lib, t: wanted.get(t.title, ())
    )


def run(db, *argv):
    return cli.main(["--library", str(db), *map(str, argv)])


def card_bytes(card):
    folder = card / "PIONEER" / "CDJ"
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_show_prints_what_is_on_the_card(db, card, capsys):
    assert run(db, "card", "show", card) == 0
    out = capsys.readouterr().out
    assert "index version 1, 3 discs" in out
    assert "#5 loop track 1 at 0:42.15" in out
    assert "(no file yet)" in out  # CD 2 and 3 are in the index with no file
    assert "ebdeddddbb" not in out  # the amplitudes only with --waveforms
    run(db, "card", "show", card, "--waveforms")
    assert "ebdeddddbb" in capsys.readouterr().out


def test_show_says_so_plainly_when_the_folder_is_not_a_card(db, tmp_path, capsys):
    assert run(db, "card", "show", tmp_path) == 1
    assert "is this the player's card" in capsys.readouterr().err


def test_backup_copies_the_files_and_changes_nothing(db, card, tmp_path, capsys):
    before = card_bytes(card)
    assert run(db, "card", "backup", card, "--backup-dir", tmp_path / "bk") == 0
    (copy,) = list((tmp_path / "bk").iterdir())
    assert {p.name: p.read_bytes() for p in copy.iterdir()} == before
    assert card_bytes(card) == before
    assert str(copy) in capsys.readouterr().out


def test_sync_without_yes_is_a_dry_run_that_writes_nothing(db, card, tmp_path, capsys):
    before = card_bytes(card)
    assert (
        run(db, "card", "sync", 1, "--card", card, "--backup-dir", tmp_path / "bk") == 0
    )
    out = capsys.readouterr().out
    assert "on the card, no file yet (card file CDJ00002)" in out
    assert "+ cue  track 1 at 0:17.00" in out  # 15 s into track 1 (position 150 + 1125)
    assert "+ loop track 12" in out
    assert "dry run" in out
    assert card_bytes(card) == before
    assert not (tmp_path / "bk").exists()


def test_sync_with_yes_backs_up_then_writes_and_a_second_run_has_nothing_to_do(
    db, card, tmp_path, capsys
):
    before = card_bytes(card)
    assert (
        run(
            db,
            "card",
            "sync",
            1,
            "--card",
            card,
            "--backup-dir",
            tmp_path / "bk",
            "--yes",
        )
        == 0
    )
    (backup,) = list((tmp_path / "bk").iterdir())
    assert {p.name: p.read_bytes() for p in backup.iterdir()} == before  # how it was

    _, files = cc.read_card(card)
    assert files["CDJ00001"] is not None  # the player's own file for CD 1, untouched
    assert card_bytes(card)["CDJ00001.PDJ"] == before["CDJ00001.PDJ"]
    written = files["CDJ00002"]
    assert [(c.track, c.is_loop) for c in written.cues] == [
        (1, False),
        (1, False),
        (12, True),
    ]
    assert written.hot_cues == [None, None, None]  # a hot cue becomes a memory cue

    capsys.readouterr()
    assert (
        run(
            db,
            "card",
            "sync",
            1,
            "--card",
            card,
            "--backup-dir",
            tmp_path / "bk",
            "--yes",
        )
        == 0
    )
    assert "Nothing to write" in capsys.readouterr().out
    assert len(list((tmp_path / "bk").iterdir())) == 1  # no second backup for nothing


def test_a_disc_that_cant_be_synced_is_named_and_nothing_is_written(
    db, card, tmp_path, capsys
):
    before = card_bytes(card)
    assert (
        run(
            db,
            "card",
            "sync",
            2,
            "--card",
            card,
            "--yes",
            "--backup-dir",
            tmp_path / "bk",
        )
        == 2
    )
    out = capsys.readouterr().out
    assert "can't sync: A disc of one track" in out
    assert card_bytes(card) == before


def test_sync_to_a_folder_that_is_not_a_card_stops_before_anything(
    db, tmp_path, capsys
):
    assert run(db, "card", "sync", 1, "--card", tmp_path, "--yes") == 1
    assert "is this the player's card" in capsys.readouterr().err
