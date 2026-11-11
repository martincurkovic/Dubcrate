from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from dubcrate import analysis
from dubcrate.analysis import (
    Cue,
    _cue_from_entry,
    analysis_file,
    build_phrases,
    phrase_label,
    read_analysis,
    read_preview,
    share_root,
)
from dubcrate.audio.devices import SAMPLE_RATE
from dubcrate.waveform import (
    BAND_HIGH,
    BAND_LOW,
    BAND_MID,
    Waveform,
    compute_waveform,
    from_bytes,
    overall_height,
    raster_columns,
    rgb_colors,
    whiteness,
)


def sine(freq, seconds=2.0, amplitude=0.5):
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


# -- the waveform type ---------------------------------------------------------


def test_from_bytes_scales_to_0_1_and_ignores_a_trailing_partial_column():
    wf = from_bytes(
        bytes([127, 0, 64, 10, 20, 30, 99]), 150
    )  # 2 columns and a stray byte
    assert wf.bands.shape == (2, 3) and wf.bands.dtype == np.float32
    assert wf.bands[0].tolist() == pytest.approx([1.0, 0.0, 64 / 127])
    assert wf.columns == 2 and wf.duration == pytest.approx(2 / 150)


def test_bytes_above_the_range_are_clamped():
    assert from_bytes(bytes([255, 255, 255]), 1).bands.max() == 1.0


def test_shrinking_keeps_the_loudest_of_each_group():
    bands = np.zeros((8, 3), dtype=np.float32)
    bands[1, 0], bands[6, 2] = 0.9, 0.7
    small = Waveform(bands, 8.0).resampled(4)
    assert small.columns == 4 and small.columns_per_second == pytest.approx(4.0)
    assert small.bands[0, 0] == pytest.approx(0.9) and small.bands[
        3, 2
    ] == pytest.approx(0.7)


def test_growing_repeats_columns():
    bands = np.array([[0.1, 0.2, 0.3], [0.9, 0.8, 0.7]], dtype=np.float32)
    big = Waveform(bands, 2.0).resampled(6)
    assert (
        big.columns == 6
        and big.bands[0].tolist() == big.bands[2].tolist()
        and big.bands[5, 0] == pytest.approx(0.9)
    )


def test_resampling_never_produces_empty_groups_or_crashes_on_odd_sizes():
    wf = Waveform(np.random.default_rng(1).random((1200, 3)).astype(np.float32), 0.0)
    for n in (1, 7, 199, 200, 1199, 1200, 3000):
        assert wf.resampled(n).columns == n
    assert wf.resampled(0).columns == 0
    assert Waveform(np.zeros((0, 3), np.float32), 1.0).resampled(10).columns == 0


# -- computing one from audio -------------------------------------------------------


@pytest.mark.parametrize(
    ("freq", "band"), [(80, BAND_LOW), (1_000, BAND_MID), (8_000, BAND_HIGH)]
)
def test_a_tone_lights_up_its_own_band_most(freq, band):
    wf = compute_waveform(sine(freq))
    means = wf.bands[10:-10].mean(axis=0)
    assert means.argmax() == band and means[band] > 3 * np.delete(means, band).max()


def test_there_is_one_column_per_150th_of_a_second():
    wf = compute_waveform(sine(440, seconds=2.0))
    assert (
        abs(wf.columns - 300) <= 1
        and wf.columns_per_second == 150
        and abs(wf.duration - 2.0) < 0.01
    )
    assert 0.0 <= wf.bands.min() and wf.bands.max() <= 1.0


def test_loud_passages_draw_taller_than_quiet_ones():
    audio = np.concatenate([sine(80, 1.0, 0.05), sine(80, 1.0, 0.8)])
    wf = compute_waveform(audio)
    half = wf.columns // 2
    assert (
        wf.bands[half + 5 : -5, BAND_LOW].mean()
        > 2 * wf.bands[5 : half - 5, BAND_LOW].mean()
    )


def test_silence_is_flat_and_does_not_divide_by_zero():
    wf = compute_waveform(np.zeros(SAMPLE_RATE, dtype=np.float32))
    assert wf.columns > 0 and not wf.bands.any()


def test_a_very_short_clip_still_gives_a_column():
    assert compute_waveform(np.ones(50, dtype=np.float32)).columns == 1


# -- phrases ----------------------------------------------------------------------------


def test_the_high_mood_names_match_what_rekordbox_shows():
    assert [phrase_label(1, k) for k in (1, 5, 3, 2, 6)] == [
        "INTRO",
        "CHORUS",
        "DOWN",
        "UP",
        "OUTRO",
    ]
    assert phrase_label(2, 8) == "BRIDGE" and phrase_label(3, 9) == "CHORUS"
    assert phrase_label(1, 99) == "PART 99" and phrase_label(7, 1) == "PART 1"


def test_phrases_get_their_times_from_the_beat_grid():
    beats = np.arange(100) * 0.5  # a beat every half second: beat 1 at 0.0 s
    got = build_phrases(
        1, [(1, 1), (17, 5), (33, 3)], end_beat=65, beat_times=beats, track_end=60.0
    )
    assert [(p.label, p.start, p.end) for p in got] == [
        ("INTRO", 0.0, 8.0),
        ("CHORUS", 8.0, 16.0),
        ("DOWN", 16.0, 32.0),
    ]


def test_a_final_phrase_that_runs_past_the_grid_ends_with_the_track():
    beats = np.arange(10) * 1.0
    [phrase] = build_phrases(
        1, [(5, 6)], end_beat=640, beat_times=beats, track_end=42.0
    )
    assert (phrase.label, phrase.start, phrase.end) == ("OUTRO", 4.0, 42.0)


def test_no_beat_grid_means_no_phrases():
    assert build_phrases(1, [(1, 1)], 10, np.zeros(0), 10.0) == ()


# -- cues (parsed defensively: none of the sampled real tracks had any) ----------------------


def test_cue_kinds():
    memory = _cue_from_entry(
        SimpleNamespace(
            time=1500, hot_cue=0, type="single", loop_time=0xFFFFFFFF, comment=""
        )
    )
    hot = _cue_from_entry(
        SimpleNamespace(
            time=2000, hot_cue=2, type="single", loop_time=0xFFFFFFFF, comment="Drop"
        )
    )
    loop = _cue_from_entry(
        SimpleNamespace(time=3000, hot_cue=0, type="loop", loop_time=5000, comment="")
    )
    assert memory == Cue("memory", 1.5)
    assert hot == Cue("hot", 2.0, None, 2, "Drop")
    assert loop == Cue("loop", 3.0, 5.0, None, "")
    assert _cue_from_entry(SimpleNamespace()) is None  # not a cue at all


# -- files ---------------------------------------------------------------------------------------


def test_share_root_sits_next_to_master_db():
    assert share_root("/x/rekordbox/master.db") == Path("/x/rekordbox/share")


def test_analysis_files_are_found_by_suffix(tmp_path):
    folder = tmp_path / "PIONEER" / "USBANLZ" / "abc" / "uuid"
    folder.mkdir(parents=True)
    (folder / "ANLZ0000.DAT").write_bytes(b"x")
    assert (
        analysis_file(tmp_path, "/PIONEER/USBANLZ/abc/uuid/ANLZ0000.DAT", ".DAT")
        == folder / "ANLZ0000.DAT"
    )
    assert (
        analysis_file(tmp_path, "/PIONEER/USBANLZ/abc/uuid/ANLZ0000.DAT", ".EXT")
        is None
    )


def test_a_track_without_analysis_files_is_simply_unanalysed(tmp_path):
    assert read_analysis(tmp_path, "/PIONEER/USBANLZ/none/ANLZ0000.DAT", 100.0) is None
    assert read_preview(tmp_path, "/PIONEER/USBANLZ/none/ANLZ0000.DAT") is None


def test_a_damaged_analysis_file_is_treated_as_no_analysis(tmp_path):
    folder = tmp_path / "PIONEER" / "x"
    folder.mkdir(parents=True)
    (folder / "ANLZ0000.2EX").write_bytes(b"definitely not an ANLZ file")
    assert read_preview(tmp_path, "/PIONEER/x/ANLZ0000.DAT") is None


LOCAL_DB = Path.home() / "Library/Pioneer/rekordbox/master.db"


@pytest.mark.skipif(
    not (LOCAL_DB.parent / "share").is_dir(), reason="no local rekordbox library"
)
def test_against_a_real_local_rekordbox_analysis():
    root = share_root(LOCAL_DB)
    candidates = sorted((root / "PIONEER" / "USBANLZ").glob("*/*/ANLZ0000.DAT"))[:20]
    assert candidates, "the local share folder has no analysis files"
    for dat in candidates:
        path = "/" + dat.relative_to(root).as_posix()
        preview = read_preview(root, path)
        if preview is None:
            continue
        got = read_analysis(root, path, track_end=300.0)
        assert preview.columns == 1200 and got is not None and got.waveform is not None
        assert (
            got.waveform.columns_per_second == 150 and got.waveform.bands.max() <= 1.0
        )
        assert len(got.beat_times) > 0 and got.bpm and 40 < got.bpm < 250
        assert all(p.end > p.start for p in got.phrases)
        return
    pytest.skip("none of the first analysis files had a 3-band preview")


def test_analysis_module_exposes_its_types():
    assert analysis.Analysis().cues == () and analysis.Analysis().waveform is None


# -- the RGB and blue styles --------------------------------------------------


def bands(*rows):
    return np.array(rows, dtype=np.float32)


def test_the_overall_height_is_the_rms_of_the_bands_and_never_above_one():
    h = overall_height(bands([0, 0, 0], [1, 0, 0], [0.6, 0.8, 0], [1, 1, 1]))
    assert h[0] == 0 and h[1] == pytest.approx(1.0)
    assert h[2] == pytest.approx(1.0)  # 0.6 and 0.8 make 1.0
    assert h[3] == 1.0  # clipped: sqrt(3) would be above one


def test_the_rgb_colour_is_red_for_lows_green_for_mids_blue_for_highs():
    red, green, blue = rgb_colors(bands([1, 0, 0], [0, 1, 0], [0, 0, 1]))
    assert red.argmax() == 0 and green.argmax() == 1 and blue.argmax() == 2
    assert red[1] == red[2] == 0  # a pure low is pure red


def test_the_rgb_colour_is_about_what_is_in_the_column_not_how_loud_it_is():
    quiet, loud = rgb_colors(bands([0.1, 0.05, 0.02], [1.0, 0.5, 0.2]))
    assert np.allclose(quiet, loud, atol=1e-5)
    assert loud.max() == pytest.approx(1.0)  # the dominant channel is always full


def test_the_rgb_gains_tame_green_and_lift_blue_like_rekordboxs_own_colours():
    even = rgb_colors(bands([1, 1, 1]))[0]
    assert even[2] > even[0] > even[1]  # blue, then red, then green (gains 1.4, 1, 0.5)


def test_a_silent_column_has_no_colour_and_no_whiteness():
    assert rgb_colors(bands([0, 0, 0]))[0].tolist() == [0, 0, 0]
    assert whiteness(bands([0, 0, 0]))[0] == 0


def test_whiteness_rises_as_the_bass_share_falls():
    bass, balanced, bright = whiteness(
        bands([1, 0.05, 0.02], [0.4, 0.4, 0.3], [0.05, 0.4, 0.8])
    )
    assert bass < balanced < bright
    assert bass == 0 and bright == 1  # the ends of the range are reached


def test_raster_bottom_aligned_columns_grow_from_the_floor():
    body = np.ones((3, 3), np.float32)
    img = raster_columns(
        np.array([0.0, 0.5, 1.0]), body, None, rows=10, extent=10, mirrored=False
    )
    assert img.shape == (10, 3, 4) and img.dtype == np.uint8
    filled = (img[..., 3] > 0).sum(axis=0)
    assert filled.tolist() == [0, 5, 10]
    assert (
        img[9, 1, 3] == 255 and img[0, 1, 3] == 0
    )  # the floor is filled, the top isn't


def test_raster_mirrored_columns_grow_both_ways_from_the_middle():
    body = np.ones((2, 3), np.float32)
    img = raster_columns(
        np.array([0.0, 1.0]), body, None, rows=10, extent=5, mirrored=True
    )
    column = img[:, 1, 3] > 0
    assert column.sum() == 10 and column[0] and column[-1]
    assert not (img[:, 0, 3] > 0).any()


def test_raster_gives_each_column_its_own_colour_and_a_lighter_core():
    body = np.array([[1, 0, 0], [0, 0, 1]], np.float32)
    core = np.array([[1, 1, 1], [0.5, 0.5, 1]], np.float32)
    img = raster_columns(
        np.array([1.0, 1.0]), body, core, rows=20, extent=20, mirrored=False,
        core_fraction=0.5,
    )  # fmt: skip
    assert img[0, 0, :3].tolist() == [255, 0, 0]  # the top of column 0 is body (red)
    assert img[19, 0, :3].tolist() == [255, 255, 255]  # near the floor, the core
    assert img[0, 1, :3].tolist() == [0, 0, 255]
    assert img[19, 1, :3].tolist() == [128, 128, 255]
