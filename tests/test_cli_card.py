"""`dubcrate card show | backup | toc | sync` (a temporary folder is the card; no ffmpeg, no drive)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, cli, cli_card
from dubcrate import cdj_card as cc
from dubcrate.analysis import Cue
from dubcrate.drive_toc import DriveTocError
from dubcrate.library import Library, TrackRecord
from dubcrate.library.rekordbox import RekordboxError

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
            "rekordbox", "/rb/master.db", "rb",
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
    """rekordbox's database, stood in for: cues on the first and last track of the 12-track disc."""
    wanted = {
        "t0": [Cue("memory", 15.0), Cue("hot", 40.0, slot=1)],
        "t11": [Cue("loop", 30.0, end=34.0)],
    }
    monkeypatch.setattr(card_discs, "read_cues", lambda _path: wanted)


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


def test_a_rekordbox_database_that_cant_be_read_is_a_plain_error_and_nothing_is_written(
    db, card, tmp_path, monkeypatch, capsys
):
    def broken(_path):
        raise RekordboxError("could not open /rb/master.db")

    monkeypatch.setattr(card_discs, "read_cues", broken)
    before = card_bytes(card)
    assert (
        run(
            db,
            "card",
            "sync",
            1,
            "--card",
            card,
            "--yes",
            "--backup-dir",
            tmp_path / "bk",
        )
        == 1
    )
    assert "could not open /rb/master.db" in capsys.readouterr().err
    assert card_bytes(card) == before


# -- the disc in the drive ------------------------------------------------------------------------------


def drive(monkeypatch, name="CD2_TOC.txt", edit=None):
    """A stand-in for the drive: the TOC saved from a real disc, optionally altered."""
    starts, lead_out = cc.toc_from_drutil((DATA / name).read_text(encoding="utf-8"))
    if edit:
        starts, lead_out = edit(starts, lead_out)
    monkeypatch.setattr(cli_card, "read_drive_toc", lambda: (starts, lead_out))


def no_drive(monkeypatch):
    def refuse():
        raise DriveTocError("There's no readable disc in the drive (nothing).")

    monkeypatch.setattr(cli_card, "read_drive_toc", refuse)


def test_toc_prints_the_disc_in_the_drive_and_its_id(db, monkeypatch, capsys):
    drive(monkeypatch)
    assert run(db, "card", "toc") == 0
    out = capsys.readouterr().out
    assert "track  1: starts 0:02.00" in out
    assert "lead-out: " in out and "12 tracks" in out


def test_toc_with_a_disc_says_when_the_plan_has_exactly_that_toc(
    db, monkeypatch, capsys
):
    drive(monkeypatch)
    assert run(db, "card", "toc", 1) == 0
    assert "has exactly this TOC" in capsys.readouterr().out


def test_toc_with_a_disc_lists_what_differs(db, monkeypatch, capsys):
    drive(monkeypatch, edit=lambda s, e: ([*s[:-1], s[-1] + 75], e + 75))
    assert run(db, "card", "toc", 1) == 1
    out = capsys.readouterr().out
    assert "NOT the disc in the drive" in out and "track 12 starts at" in out


def test_toc_with_no_disc_in_the_drive_is_a_plain_error(db, monkeypatch, capsys):
    no_drive(monkeypatch)
    assert run(db, "card", "toc") == 1
    assert "no readable disc" in capsys.readouterr().err


def test_toc_refuses_a_data_disc_id(db, monkeypatch, capsys):
    drive(monkeypatch)
    with Library.open(db) as lib:
        data = lib.create_disc("Data", kind="mp3")
    assert run(db, "card", "toc", data) == 1
    assert "not an audio disc" in capsys.readouterr().err


def test_sync_from_drive_writes_the_entry_for_the_drives_toc(
    db, card, tmp_path, monkeypatch, capsys
):
    starts, lead_out = cc.toc_from_drutil(
        (DATA / "CD2_TOC.txt").read_text(encoding="utf-8")
    )
    edited = ([*starts[:-1], starts[-1] + 75], lead_out + 75)
    drive(monkeypatch, edit=lambda *_: edited)
    argv = ("card", "sync", 1, "--card", card, "--backup-dir", tmp_path / "bk")
    assert run(db, *argv, "--from-drive", "--yes") == 0
    assert (
        "isn't the disc as planned" in capsys.readouterr().out
    )  # and it follows the drive
    index, _ = cc.read_card(card)
    assert cc.DiscId.from_toc(*edited) in [entry.disc_id for entry in index.entries]


def test_sync_from_drive_is_for_one_disc_and_needs_a_disc(
    db, card, monkeypatch, capsys
):
    drive(monkeypatch)
    assert run(db, "card", "sync", 1, 2, "--card", card, "--from-drive") == 1
    assert "one disc" in capsys.readouterr().err
    no_drive(monkeypatch)
    before = card_bytes(card)
    assert run(db, "card", "sync", 1, "--card", card, "--from-drive", "--yes") == 1
    assert "no readable disc" in capsys.readouterr().err
    assert card_bytes(card) == before


def test_sync_without_the_flag_never_asks_the_drive(db, card, monkeypatch):
    def refuse():
        raise AssertionError("must not read the drive")

    monkeypatch.setattr(cli_card, "read_drive_toc", refuse)
    assert run(db, "card", "sync", 1, "--card", card) == 0
