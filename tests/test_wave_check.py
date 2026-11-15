"""Comparing the card's waveform records with the model: the numbers, the played columns, the command."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from dubcrate import cdj_card as cc
from dubcrate import cli, wave_check, wave_model, wavetest

DATA = Path(__file__).parent / "data" / "cdj_card"
PLAYER = cc.TrackFile.parse((DATA / "disc4_probes_and_music.PDJ").read_bytes())
PROBES = {p.number: p for p in wavetest.build_probe_tracks(1)}


def test_a_perfect_model_is_all_exact_and_a_biased_one_says_which_way():
    values = [3, 5, 7, 9] * 25
    same = wave_check.compare(1, values, values)
    assert (same.exact, same.within1, same.mean_error, same.bias) == (
        1.0,
        1.0,
        0.0,
        0.0,
    )
    high = wave_check.compare(1, values, [v + 2 for v in values])
    assert high.exact == 0 and high.within1 == 0 and high.within2 == 1
    assert high.mean_error == 2 and high.bias == 2


def test_only_the_columns_the_player_filled_are_compared():
    half = [4] * 50 + [0] * 50
    c = wave_check.compare(1, half, [4] * 100)
    assert c.columns == 50 and c.exact == 1.0
    assert wave_check.compare(1, [0] * 100, [4] * 100) is None  # nothing played


def test_the_model_against_the_players_own_record_of_a_probe_track():
    comparisons, notes = wave_check.check_file(
        PLAYER,
        lambda t: wavetest.render(PROBES[t]) if t in PROBES else None,
        tracks=[3, 6],
    )
    assert [c.track for c in comparisons] == [3, 6] and notes == []
    assert all(
        c.within1 >= 0.9 for c in comparisons
    )  # (a level ladder, 125 Hz in the channels)


def test_tracks_with_no_audio_or_no_record_are_said_not_skipped_silently():
    empty = cc.TrackFile.new(PLAYER.disc_id)
    empty.fine = [cc.WaveRecord.unplayed(), PLAYER.fine[2]]
    comparisons, notes = wave_check.check_file(empty, lambda t: None)
    assert comparisons == []
    assert "track 1: the player has not recorded it" in notes
    assert "track 2: no audio to compare with" in notes


def test_the_pooled_line_weights_each_track_by_its_columns():
    a = wave_check.compare(1, [4] * 100, [4] * 100)
    b = wave_check.compare(2, [4] * 50 + [0] * 50, [6] * 100)
    total = wave_check.summary([a, b])
    assert total.columns == 150 and total.exact == pytest.approx(100 / 150)
    lines = wave_check.describe([a, b], ["track 3: no audio to compare with"])
    assert lines[0].startswith("track") and any(
        line.startswith("  all") for line in lines
    )
    assert "  track 3: no audio to compare with" in lines


def test_audio_files_are_found_by_the_number_they_begin_with(tmp_path):
    for name in (
        "1 Audio Track.aiff",
        "03 Title.flac",
        "10 Audio Track.aiff",
        ".hidden",
        "notes.txt",
    ):
        (tmp_path / name).write_bytes(b"x")
    found = wave_check.audio_files(tmp_path)
    assert sorted(found) == [1, 3, 10]
    assert found[3].name == "03 Title.flac"


def test_the_command_compares_a_disc_on_a_card_with_its_audio(tmp_path, capsys):
    toc = (DATA / "CD1_TOC.txt").read_text(encoding="utf-8")
    starts, lead_out = cc.toc_from_drutil(toc)
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    shutil.copy(DATA / "index_three_discs.PDJ", folder / cc.INDEX_NAME)
    shutil.copy(
        DATA / "disc1_full.PDJ", folder / "CDJ00001.PDJ"
    )  # the player's record of CD 1, track 1 only
    audio = tmp_path / "audio"
    audio.mkdir()
    tone = np.sin(2 * np.pi * 125 * np.arange(44100 * 20) / 44100) * 0.3
    wavetest.write_wav(audio / "1 Audio Track.wav", (np.stack([tone, tone], axis=1) * 32767).astype(np.int16))  # fmt: skip
    toc_file = tmp_path / "toc.txt"
    toc_file.write_text(toc, encoding="utf-8")
    code = cli.main(["--library", str(tmp_path / "x.db"), "card", "check-waveforms", str(tmp_path / "card"), "--audio", str(audio), "--toc", str(toc_file)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and "CDJ00001" in out and "   1 " in out and "all" in out
    assert cc.DiscId.from_toc(starts, lead_out) is not None


def test_a_disc_not_on_the_card_or_not_played_is_an_error_with_the_way_out(
    tmp_path, capsys
):
    toc_file = tmp_path / "toc.txt"
    toc_file.write_text(
        (DATA / "CD3_TOC.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    (folder / cc.INDEX_NAME).write_bytes(cc.IndexFile().to_bytes())
    args = ["--library", str(tmp_path / "x.db"), "card", "check-waveforms", str(tmp_path / "card"), "--audio", str(tmp_path), "--toc", str(toc_file)]  # fmt: skip
    assert cli.main(args) == 1
    assert "not on the card" in capsys.readouterr().err
    index = cc.IndexFile()
    index.add(
        cc.DiscId.from_toc(*cc.toc_from_drutil(toc_file.read_text(encoding="utf-8")))
    )
    (folder / cc.INDEX_NAME).write_bytes(index.to_bytes())
    assert cli.main(args) == 1
    assert "play some of the disc" in capsys.readouterr().err


_ = wave_model  # (the model is what check_file runs)
