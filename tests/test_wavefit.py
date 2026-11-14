"""Reading the player's waveform records against the test disc (a stand-in player with a known rule)."""

import dataclasses
import re

import numpy as np
import pytest

from dubcrate import cdj_card as cc
from dubcrate import wavefit
from dubcrate.wavetest import (
    FINE,
    Segment,
    TrackSpec,
    build_tracks,
    db,
    manifest,
    render,
)

TRACKS = build_tracks()
BY_NUMBER = {t.number: t for t in TRACKS}
# the boundaries (dBFS) of a pretend player that counts how many of these the level reaches: values 0 to 15
THRESHOLDS = [
    -85.5,
    -73.5,
    -64.5,
    -55.5,
    -49.5,
    -43.5,
    -37.5,
    -31.5,
    -25.5,
    -19.5,
    -13.5,
    -10.5,
    -7.5,
    -4.5,
    -1.5,
]
DISC = cc.DiscId.from_toc(*cc.toc_from_lengths([5000, 5000, 5000]))


def played_file(
    numbers, statistic="peak_lr", offset=0.0, thresholds=THRESHOLDS
) -> cc.TrackFile:
    """A card file as a player with this rule would leave it after playing these tracks."""
    coarse, fine = [], []
    for number in numbers:
        audio = render(BY_NUMBER[number])
        coarse.append(
            cc.WaveRecord.coarse(
                number,
                wavefit.simulate_player(
                    audio, thresholds, statistic, 50, offset
                ).tolist(),
            )
        )
        fine.append(
            cc.WaveRecord.fine(
                number,
                wavefit.simulate_player(
                    audio, thresholds, statistic, FINE, offset
                ).tolist(),
            )
        )
    return dataclasses.replace(cc.TrackFile.new(DISC), coarse=coarse, fine=fine)


def test_a_records_values_are_read_back_as_50_and_as_100_nibbles():
    levels = list(range(16)) * 3 + [1, 2]
    coarse = cc.WaveRecord.coarse(1, levels[:50])
    assert list(wavefit.levels_of(coarse, False)) == levels[:50]
    fine = cc.WaveRecord.fine(1, (list(range(16)) * 7)[:100])
    assert list(wavefit.levels_of(fine, True)) == (list(range(16)) * 7)[:100]


def test_column_statistics_of_a_known_signal():
    spec = TrackSpec(
        90, "t", 4.0, "", (Segment(0, 4.0, "sine", {"freq": 1000.0, "peak_db": -6.0}),)
    )
    stats = wavefit.column_stats(render(spec), 4)
    assert stats["peak_lr"] == pytest.approx([db(-6.0)] * 4, abs=0.0005)
    assert stats["rms_mono"] == pytest.approx([db(-6.0) / np.sqrt(2)] * 4, abs=0.001)
    assert stats["meanabs_mono"] == pytest.approx([db(-6.0) * 2 / np.pi] * 4, abs=0.001)


def test_a_shifted_grid_moves_where_the_columns_begin():
    burst = TrackSpec(
        91,
        "t",
        4.0,
        "",
        (
            Segment(0, 4.0, "silence"),
            Segment(2.0, 3.0, "sine", {"freq": 1000.0, "peak_db": 0.0}),
        ),
    )
    audio = render(burst)
    assert wavefit.column_stats(audio, 4)["peak_lr"].round(1).tolist() == [0, 0, 1.0, 0]
    # a grid whose columns begin a second later sees the burst a column earlier, and one a second earlier, later
    assert wavefit.column_stats(audio, 4, offset=1.0)["peak_lr"].round(1).tolist() == [
        0,
        1.0,
        0,
        0,
    ]
    assert wavefit.column_stats(audio, 4, offset=-1.0)["peak_lr"].round(1).tolist() == [
        0,
        0,
        0,
        1.0,
    ]


def test_rank_correlation_and_discordance():
    x = np.array([1.0, 2, 3, 4, 5])
    assert wavefit.spearman(x, x * 2) == pytest.approx(1.0)
    assert wavefit.spearman(x, -x) == pytest.approx(-1.0)
    assert wavefit.spearman(x, np.ones(5)) == 0.0
    levels = np.array([0, 0, 1, 2, 2])
    assert wavefit.discordance(np.array([-60.0, -50, -40, -30, -20]), levels) == 0.0
    assert wavefit.discordance(np.array([-20.0, -30, -40, -50, -60]), levels) == 1.0


def test_columns_inside_a_segment_exclude_those_that_straddle_its_edge():
    seg = Segment(4.0, 8.0, "sine")
    assert wavefit.columns_inside(FINE, 100.0, 0.0, seg) == [4, 5, 6, 7]
    assert wavefit.columns_inside(FINE, 100.0, 0.5, seg) == [
        4,
        5,
        6,
    ]  # (the grid has moved half a column)


def test_a_track_that_was_never_played_is_reported_and_not_measured():
    file = played_file([1])
    _fit, lines = wavefit.analyse(TRACKS, file)
    assert lines[0] == "Tracks played (a record with any value set): 1"
    assert "Not played" in lines[1] and "2, 3" in lines[1]


def test_the_fit_finds_a_peak_rule_and_the_boundaries_of_the_scale():
    file = played_file([1, 2, 6, 9, 14])
    fit, lines = wavefit.analyse(TRACKS, file)
    assert fit.statistic == "peak_lr"
    assert fit.discordance["peak_lr"] < 0.02
    assert (
        fit.discordance["rms_mono"] > fit.discordance["peak_lr"] + 0.02
    )  # (the equal-RMS shapes tell them apart)
    # each estimated boundary is near the real one, the loud ones closely
    assert len(fit.thresholds_db) >= 12
    for estimate, truth in zip(fit.thresholds_db[-9:], THRESHOLDS[-9:]):
        assert estimate == pytest.approx(truth, abs=1.6)
    assert any("Scale on peak_lr" in line for line in lines)


def test_the_fit_tells_an_rms_rule_from_a_peak_rule():
    file = played_file([1, 2, 6, 9, 14], statistic="rms_lr")
    fit, _lines = wavefit.analyse(TRACKS, file)
    assert fit.statistic in (
        "rms_lr",
        "rms_mono",
    )  # (same for these two-equal-channel tracks)
    assert fit.discordance[fit.statistic] < 0.02 < fit.discordance["peak_lr"]


def test_the_fit_finds_where_the_players_columns_begin_from_the_burst_track():
    for shift in (0.0, 0.25, -0.375):  # seconds later or earlier than the nominal grid
        fit, lines = wavefit.analyse(TRACKS, played_file([3, 1], offset=shift))
        assert fit.offsets[3] == pytest.approx(shift, abs=0.13), shift
    assert any("shifted" in line for line in lines)


def test_without_the_burst_track_the_columns_are_taken_to_start_with_the_track():
    fit, lines = wavefit.analyse(TRACKS, played_file([1, 2]))
    assert fit.offsets == {} and any(
        "alignment track (3) was not played" in line for line in lines
    )


def test_the_report_has_a_line_per_segment_with_the_players_value():
    _fit, lines = wavefit.analyse(TRACKS, played_file([6]))

    def value(label: str) -> int:
        line = next(line for line in lines if label in line)
        return int(re.search(r"\s(\d+) \[", line).group(1))

    # a peak-reading player gives pulses of the same RMS a higher value than the sine
    assert value("1% pulses, RMS -20") > value("sine, RMS -20")
    assert "peak" in next(line for line in lines if "sine, RMS -20" in line)


# -- the command line ----------------------------------------------------------------------------------------


def load_spike():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parent.parent / "spikes" / "wave_test_disc.py"
    spec = importlib.util.spec_from_file_location("wave_test_disc_spike", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def card_with(tmp_path, tracks_played, disc_id, **kw):
    """A card folder holding the test disc's file as a player would have left it."""
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    file = dataclasses.replace(
        played_file(tracks_played, **kw), disc_id=disc_id.with_flags(cc.FILE_FLAGS)
    )
    index = cc.IndexFile([cc.IndexEntry("CDJ00001", disc_id)])
    cc.write_card(tmp_path / "card", index, {"CDJ00001": file}, create=True)
    return tmp_path / "card"


def test_the_fit_command_reads_the_manifest_finds_the_disc_on_the_card_and_prints_the_report(
    tmp_path, capsys
):
    import json

    spike = load_spike()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest(TRACKS)), encoding="utf-8")
    disc_id = cc.DiscId.from_toc(*cc.toc_from_lengths([t.sectors for t in TRACKS]))
    card = card_with(tmp_path, [1, 2, 3, 6, 9], disc_id)
    import argparse

    assert (
        spike.fit(argparse.Namespace(manifest=str(manifest_file), card=str(card))) == 0
    )
    out = capsys.readouterr().out
    assert (
        "Tracks played" in out
        and "Scale on peak_lr" in out
        and "Statistics, best first" in out
    )
    assert "note:" not in out  # (the planned TOC was the card's entry)


def test_the_fit_command_takes_the_only_disc_of_the_right_size_when_the_toc_differs(
    tmp_path, capsys
):
    import argparse
    import json

    spike = load_spike()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest(TRACKS)), encoding="utf-8")
    real = cc.DiscId.from_toc(
        *cc.toc_from_lengths([t.sectors for t in TRACKS], gap=1)
    )  # (a different TOC)
    card = card_with(tmp_path, [1], real)
    assert (
        spike.fit(argparse.Namespace(manifest=str(manifest_file), card=str(card))) == 0
    )
    assert "differs from the planned TOC" in capsys.readouterr().out


def test_the_fit_command_says_so_when_the_disc_was_never_played_or_is_not_there(
    tmp_path, capsys
):
    import argparse
    import json

    spike = load_spike()
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest(TRACKS)), encoding="utf-8")
    folder = tmp_path / "card" / "PIONEER" / "CDJ"
    folder.mkdir(parents=True)
    disc_id = cc.DiscId.from_toc(*cc.toc_from_lengths([t.sectors for t in TRACKS]))
    cc.write_card(
        tmp_path / "card",
        cc.IndexFile([cc.IndexEntry("CDJ00001", disc_id)]),
        {},
        create=True,
    )
    assert (
        spike.fit(
            argparse.Namespace(manifest=str(manifest_file), card=str(tmp_path / "card"))
        )
        == 1
    )
    assert "has not been played" in capsys.readouterr().err
    cc.write_card(tmp_path / "card", cc.IndexFile([]), {}, create=True)
    assert (
        spike.fit(
            argparse.Namespace(manifest=str(manifest_file), card=str(tmp_path / "card"))
        )
        == 1
    )
    assert "no disc of 20 tracks" in capsys.readouterr().err
