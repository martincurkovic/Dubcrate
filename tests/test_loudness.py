"""Level matching: measuring loudness, the gain maths, and that a rendered track really moves by its gain."""

import shutil
import subprocess

import pytest

from dubcrate import loudness, settings, tools
from dubcrate.build import (
    BurnPlan,
    PlanTrack,
    apply_levels,
    planned_with_levels,
    render_plan,
)
from dubcrate.library import Library, TrackRecord
from dubcrate.loudness import (
    Gain,
    Loudness,
    Target,
    describe_gains,
    gain_for,
    measure,
    parse_ebur128,
)
from dubcrate.render import RenderError, ToolMissingError, render_track

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)

# the closing summary of `ffmpeg -af ebur128=peak=true` (ffmpeg 9.0.2)
SUMMARY = """\
[Parsed_ebur128_0 @ 0x600] Summary:

  Integrated loudness:
    I:         -33.8 LUFS
    Threshold: -43.8 LUFS

  Loudness range:
    LRA:        20.0 LU
    Threshold: -53.8 LUFS
    LRA low:   -53.8 LUFS
    LRA high:  -33.8 LUFS

  True peak:
    Peak:      -30.1 dBFS
"""


# -- reading ebur128 ------------------------------------------------------------------------


def test_the_summary_gives_the_loudness_and_the_true_peak():
    assert parse_ebur128(SUMMARY) == Loudness(-33.8, -30.1)


def test_silence_is_a_loudness_of_nothing_not_an_error():
    silent = SUMMARY.replace("-33.8 LUFS", "-inf LUFS").replace(
        "-30.1 dBFS", "-inf dBFS"
    )
    assert parse_ebur128(silent) == Loudness(loudness.SILENT_LUFS, loudness.SILENT_LUFS)


def test_output_with_no_summary_is_nothing():
    assert parse_ebur128("Error opening input file") is None


# -- the gain -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("measured", "gain", "wanted", "limited"),
    [
        (Loudness(-20, -12), 6.0, 6.0, False),  # lifted to the target
        (Loudness(-8, -1.0), -6.0, -6.0, False),  # a loud master is turned down
        (Loudness(-14, -3), 0.0, 0.0, False),  # already there
        (Loudness(-20, -6), 5.7, 6.0, True),  # peaks stop it short of the target
        (
            Loudness(-20, 0.5),
            0.0,
            6.0,
            True,
        ),  # peaks already over the ceiling: not turned DOWN for it
        (
            Loudness(-40, -30),
            12.0,
            26.0,
            True,
        ),  # a very quiet file is not lifted past the cap
        (Loudness(loudness.SILENT_LUFS, loudness.SILENT_LUFS), 0.0, 0.0, False),
    ],
)
def test_gain_reaches_the_target_without_clipping(measured, gain, wanted, limited):
    got = gain_for(measured, Target(-14))
    assert (got.db, got.wanted_db, got.limited) == (gain, wanted, limited)
    if got.db > 0:  # whatever the gain, the peak stays under the ceiling
        assert measured.true_peak_db + got.db <= loudness.PEAK_CEILING_DB + 0.01


@pytest.mark.parametrize(
    ("measured", "gain", "wanted", "limited"),
    [
        (Loudness(-20, -12), 11.7, 11.7, False),  # the peak goes up to the ceiling
        (Loudness(-8, -3), 2.7, 2.7, False),  # however loud the track already is
        (Loudness(-8, -0.3), 0.0, 0.0, False),  # already there
        (Loudness(-8, 0.5), -0.8, -0.8, False),  # peaks over the ceiling come down to it
        (Loudness(-40, -30), 12.0, 29.7, True),  # a very quiet file is not lifted past the cap
        (Loudness(loudness.SILENT_LUFS, loudness.SILENT_LUFS), 0.0, 0.0, False),
    ],
)  # fmt: skip
def test_peak_normalising_puts_the_highest_peak_at_the_ceiling(measured, gain, wanted, limited):  # fmt: skip
    got = gain_for(measured, loudness.PEAK_TARGET)
    assert (got.db, got.wanted_db, got.limited) == (gain, wanted, limited)


def test_peak_normalising_ignores_how_loud_a_track_is():
    a = gain_for(Loudness(-20, -10), loudness.PEAK_TARGET)
    b = gain_for(Loudness(-6, -10), loudness.PEAK_TARGET)
    assert a == b  # the same peak, the same gain: it is not loudness matching


def test_the_gains_are_described_in_one_line():
    assert (
        describe_gains([Gain(5, 6), Gain(-6, -6), Gain(0, 0)])
        == "gains -6.0 to +5.0 dB across 3 tracks · 1 held back by peak headroom"
    )
    assert describe_gains([Gain(2, 2)]) == "gain +2.0 dB across 1 track"
    assert describe_gains([Gain(2, 2)], unmeasured=2).endswith(
        "· 2 not measured (left as they are)"
    )
    assert describe_gains([]) == "No tracks were measured."
    capped = describe_gains([Gain(12, 29.7)], target=loudness.PEAK_TARGET)
    assert capped.endswith("· 1 held back by the 12 dB limit")  # (not "peak headroom")


# -- the presets ---------------------------------------------------------------------------


def test_the_presets_are_a_short_list_with_peak_first_and_a_default_that_exists():
    keys = [p.key for p in loudness.PRESETS]
    assert keys == ["peak", "-9", "-10", "-12", "-14"] and len(set(keys)) == len(keys)
    assert loudness.PRESETS[0].target is loudness.PEAK_TARGET
    default = loudness.preset_for(loudness.DEFAULT_PRESET)
    assert default.target == Target(loudness.DEFAULT_TARGET_LUFS)
    for p in loudness.PRESETS[1:]:
        assert p.target.lufs == float(p.key) and p.key in p.label  # (the label says it)
        assert loudness.TARGET_RANGE[0] <= p.target.lufs <= loudness.TARGET_RANGE[1]
    assert "full scale" in loudness.PRESETS[0].label


def test_an_unknown_preset_is_the_default_and_an_old_target_becomes_the_nearest():
    assert loudness.preset_for("nonsense").key == loudness.DEFAULT_PRESET
    assert loudness.preset_for(-14).key == "-14"  # (a number works as its key)
    assert [loudness.nearest_preset(x).key for x in (-6, -9.4, -11.5, -15, -24)] == [
        "-9", "-9", "-12", "-14", "-14",
    ]  # fmt: skip


def test_a_target_says_what_it_is():
    assert str(Target(-10)) == "-10 LUFS"
    assert (
        str(loudness.PEAK_TARGET) == "peak at -0.3 dBTP" and loudness.PEAK_TARGET.peak
    )


# -- settings -----------------------------------------------------------------------------


def test_the_settings_default_to_off_with_the_club_preset(qapp):
    assert settings.get_levels_enabled() is False
    assert settings.get_levels_choice() == "-10" == loudness.DEFAULT_PRESET
    assert settings.get_levels_target() is None  # off: nothing to bring the tracks to
    settings.set_levels_enabled(True)
    assert settings.get_levels_target() == Target(-10)
    settings.set_levels_choice("peak")
    assert settings.get_levels_target() is loudness.PEAK_TARGET
    settings.set_levels_enabled(False)
    assert settings.get_levels_target() is None
    assert settings.get_levels_choice() == "peak"  # (what was chosen is kept while off)
    settings.set_levels_choice("nonsense")  # nonsense is the default
    assert settings.get_levels_choice() == "-10"


def test_settings_saved_before_the_presets_carry_over(qapp):
    qs = settings._qs()
    qs.setValue("levels/mode", "match")  # how it was kept: a mode and any LUFS
    qs.setValue("levels/target", -15)
    assert settings.get_levels_enabled() is True
    assert settings.get_levels_choice() == "-14"
    qs.setValue("levels/mode", "off")
    assert settings.get_levels_enabled() is False
    qs.setValue("levels/target", "junk")
    assert settings.get_levels_choice() == "-10"
    settings.set_levels_enabled(True)  # once the new keys are written they win
    settings.set_levels_choice("-9")
    qs.setValue("levels/mode", "off")
    qs.setValue("levels/target", -20)
    assert settings.get_levels_target() == Target(-9)


def test_the_settings_window_shows_and_remembers_the_choice(qapp):
    from dubcrate.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog()
    assert [dialog.levels_combo.itemData(i) for i in range(5)] == ["peak", "-9", "-10", "-12", "-14"]  # fmt: skip
    assert not dialog.levels_check.isChecked() and not dialog.levels_combo.isEnabled()
    assert dialog.levels_combo.currentData() == "-10"
    dialog.levels_check.setChecked(True)
    assert settings.get_levels_enabled() and dialog.levels_combo.isEnabled()
    index = dialog.levels_combo.findData("peak")
    dialog.levels_combo.setCurrentIndex(index)
    dialog.levels_combo.activated.emit(index)
    assert settings.get_levels_target() is loudness.PEAK_TARGET
    again = SettingsDialog()  # (a new window shows what was chosen)
    assert again.levels_check.isChecked() and again.levels_combo.currentData() == "peak"
    assert again.levels_combo.isEnabled()
    dialog.levels_check.setChecked(False)
    assert not settings.get_levels_enabled() and not dialog.levels_combo.isEnabled()
    for d in (dialog, again):
        d.deleteLater()


# -- the library remembers a measurement until the file changes ---------------------------------


def rec(ext_id, **kw):
    return TrackRecord(ext_id=ext_id, path=f"/nonexistent/{ext_id}.mp3", title=ext_id, duration=100, **kw)  # fmt: skip


def test_a_measurement_is_kept_until_the_file_changes_size():
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2000)])  # fmt: skip
        assert all(t.loudness is None for t in lib.tracks())
        for t in lib.tracks():
            lib.set_loudness(t.id, -9.5, -0.8)
        assert lib.tracks()[0].loudness == Loudness(-9.5, -0.8)
        lib.replace_source("folder", "/m", "M", [rec("a", file_size=1000), rec("b", file_size=2500)])  # fmt: skip
        assert {t.ext_id: t.loudness for t in lib.tracks()} == {"a": Loudness(-9.5, -0.8), "b": None}  # fmt: skip


# -- the plan (no ffmpeg) ---------------------------------------------------------------------


def plan(kind="audio", ids=(1, 2, 3)):
    return BurnPlan(
        disc_id=1, name="D", performer="P", pregap_sectors=0, capacity_sectors=360_000,
        tracks=tuple(PlanTrack(i, f"/m/{i}.flac", f"T{i}", "A") for i in ids),
        kind=kind,
    )  # fmt: skip


def test_each_measured_track_gets_a_gain_and_the_note_says_what_happened():
    measured = {
        1: Loudness(-20, -12),
        2: Loudness(-8, -1.0),
    }  # track 3 was not measured
    got = apply_levels(plan(), measured, Target(-14))
    assert [t.gain_db for t in got.tracks] == [6.0, -6.0, 0.0]
    assert got.levels_note.startswith(
        "Levels matched to -14 LUFS: gains -6.0 to +6.0 dB"
    )
    assert "1 not measured" in got.levels_note
    assert [t.gain_db for t in plan().tracks] == [
        0.0,
        0.0,
        0.0,
    ]  # the plan itself is not changed


def test_a_track_twice_on_a_disc_is_one_measurement_with_one_gain():
    got = apply_levels(
        plan(ids=(1, 1, 2)),
        {1: Loudness(-20, -12), 2: Loudness(-14, -3)},
        Target(-14),
    )
    assert [t.gain_db for t in got.tracks] == [6.0, 6.0, 0.0]


def test_peak_normalising_gains_and_its_note():
    measured = {1: Loudness(-20, -12), 2: Loudness(-8, 0.5), 3: Loudness(-40, -30)}
    got = apply_levels(plan(), measured, loudness.PEAK_TARGET)
    assert [t.gain_db for t in got.tracks] == [11.7, -0.8, 12.0]
    assert got.levels_note.startswith("Peak-normalised to -0.3 dBTP: gains -0.8 to +12.0 dB")  # fmt: skip
    assert "1 held back by the 12 dB limit" in got.levels_note


def test_an_mp3_disc_is_left_alone():
    mp3_plan = plan(kind="mp3")
    assert apply_levels(mp3_plan, {1: Loudness(-20, -12)}, Target(-14)) is mp3_plan


# -- with ffmpeg: the real thing ----------------------------------------------------------------


def make_tone(path, db, seconds=3, channels=2):
    """A sine whose peak is `db` dBFS, as a FLAC. (ffmpeg's `sine` starts at 1/8 of full scale,
    -18.06 dBFS, so the volume filter is given the difference.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency=997:duration={seconds},volume={db + 18.06:.2f}dB", "-ac", str(channels), str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("gain", [4.5, -6.0])
def test_a_rendered_track_moves_by_its_gain(tmp_path, channels, gain):
    """The proof: measure a file, render it with a gain, measure the result: it moved by the gain
    (a mono file included, which is measured as the stereo it becomes)."""
    src = make_tone(tmp_path / "t.flac", -24, channels=channels)
    before = measure(src)
    render_track(src, tmp_path / "t.wav", gain)
    after = measure(tmp_path / "t.wav")
    assert after.lufs == pytest.approx(before.lufs + gain, abs=0.2)
    assert after.true_peak_db == pytest.approx(before.true_peak_db + gain, abs=0.3)


@needs_ffmpeg
def test_a_gain_changes_neither_the_length_nor_a_track_left_at_zero(tmp_path):
    src = make_tone(tmp_path / "t.flac", -20)
    plain = render_track(src, tmp_path / "plain.wav")
    assert render_track(src, tmp_path / "gained.wav", 5.0) == plain == 225
    assert measure(tmp_path / "plain.wav").lufs == pytest.approx(
        measure(src).lufs, abs=0.1
    )


@needs_ffmpeg
def test_measuring_a_file_that_is_not_audio_is_an_expected_error(tmp_path):
    (tmp_path / "bad.mp3").write_bytes(b"not audio")
    with pytest.raises(RenderError, match="couldn't measure"):
        measure(tmp_path / "bad.mp3")


def test_measuring_without_ffmpeg_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "BUNDLE_DIR", tmp_path / "no-bundle")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    with pytest.raises(ToolMissingError):
        measure(tmp_path / "a.mp3")


@pytest.fixture
def disc_of_two_levels(tmp_path):
    """A quiet track and a loud one."""
    make_tone(tmp_path / "music" / "quiet.flac", -22)
    make_tone(tmp_path / "music" / "loud.flac", -8)
    from dubcrate.library.folder import scan_folder

    scan = scan_folder(tmp_path / "music")
    with Library.open(":memory:") as lib:
        lib.replace_source(
            "folder", str(tmp_path / "music"), "m", scan.tracks, scan.nodes
        )
        disc = lib.create_disc("Levels")
        lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="title")])
        yield lib, disc


@needs_ffmpeg
def test_two_tracks_at_different_levels_come_out_matched(disc_of_two_levels, tmp_path):
    lib, disc = disc_of_two_levels
    plan_ = planned_with_levels(lib, disc, Target(-14))
    assert [t.title for t in plan_.tracks] == ["loud", "quiet"]
    assert (
        plan_.tracks[0].gain_db < 0 < plan_.tracks[1].gain_db
    )  # down for one, up for the other
    rendered = render_plan(plan_, tmp_path / "out")
    levels = [measure(t.wav).lufs for t in rendered.disc.tracks]
    assert levels == [pytest.approx(-14, abs=0.3)] * 2
    assert all(
        t.loudness for t in lib.tracks()
    )  # remembered: the next burn measures nothing


@needs_ffmpeg
def test_peak_normalising_brings_a_loud_track_to_the_ceiling_and_holds_a_quiet_one_at_the_cap(disc_of_two_levels, tmp_path):  # fmt: skip
    lib, disc = disc_of_two_levels
    plan_ = planned_with_levels(lib, disc, loudness.PEAK_TARGET)
    assert plan_.levels_note.startswith("Peak-normalised to -0.3 dBTP")
    assert "1 held back by the 12 dB limit" in plan_.levels_note  # the quiet one
    rendered = render_plan(plan_, tmp_path / "out")
    loud, quiet = (measure(t.wav).true_peak_db for t in rendered.disc.tracks)
    assert loud == pytest.approx(loudness.PEAK_CEILING_DB, abs=0.3)  # up to the ceiling
    was = measure(tmp_path / "music" / "quiet.flac").true_peak_db
    assert quiet == pytest.approx(was + loudness.MAX_GAIN_DB, abs=0.3)  # and no further


@needs_ffmpeg
def test_matching_off_renders_the_tracks_as_they_are(disc_of_two_levels, tmp_path):
    lib, disc = disc_of_two_levels
    plan_ = planned_with_levels(lib, disc, None)
    assert [t.gain_db for t in plan_.tracks] == [0.0, 0.0] and plan_.levels_note == ""
    assert all(t.loudness is None for t in lib.tracks())  # and nothing was measured


@needs_ffmpeg
def test_the_command_line_can_export_with_levels_matched(disc_of_two_levels, tmp_path):
    from dubcrate import cli

    lib, _disc = disc_of_two_levels
    db = tmp_path / "cli.db"
    with Library.open(db) as cli_lib:  # the command line opens its own library file
        cli_lib.replace_source("folder", str(tmp_path / "music"), "m", [t for t in _records(lib)], [])  # fmt: skip
        d = cli_lib.create_disc("CLI")
        cli_lib.add_to_disc(d, [t.id for t in cli_lib.tracks(sort="title")])
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["--library", str(db), "export", str(d), str(out), "--levels", "match", "--target", "-16"]) == 0  # fmt: skip
    folder = out / "CLI export"
    assert "Levels matched to -16 LUFS" in (folder / "README.txt").read_text(
        encoding="utf-8"
    )
    wavs = sorted(folder.glob("*.wav"))
    assert [measure(w).lufs for w in wavs] == [pytest.approx(-16, abs=0.3)] * 2


@needs_ffmpeg
def test_the_command_line_can_export_peak_normalised(disc_of_two_levels, tmp_path):
    from dubcrate import cli

    lib, _disc = disc_of_two_levels
    db = tmp_path / "cli.db"
    with Library.open(db) as cli_lib:
        cli_lib.replace_source("folder", str(tmp_path / "music"), "m", _records(lib), [])  # fmt: skip
        d = cli_lib.create_disc("CLI")
        cli_lib.add_to_disc(d, [t.id for t in cli_lib.tracks(sort="title")])
    out = tmp_path / "out"
    out.mkdir()
    assert cli.main(["--library", str(db), "export", str(d), str(out), "--levels", "peak"]) == 0  # fmt: skip
    folder = out / "CLI export"
    assert "Peak-normalised to -0.3 dBTP" in (folder / "README.txt").read_text(encoding="utf-8")  # fmt: skip
    loud = measure(min(folder.glob("*.wav"))).true_peak_db
    assert loud == pytest.approx(loudness.PEAK_CEILING_DB, abs=0.3)


def _records(lib):
    return [
        TrackRecord(ext_id=t.ext_id, path=t.path, title=t.title, duration=t.duration, file_size=t.file_size)
        for t in lib.tracks()
    ]  # fmt: skip
