"""A burn works out each track's CDJ waveform from the audio it has just rendered, so Sync has nothing to analyse."""

import shutil
import subprocess

import pytest

from dubcrate import build, card_discs, wave_model
from dubcrate.build import (
    BurnPlan,
    PlanTrack,
    plan_burn,
    remember_rendered,
    render_plan,
)
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def make_bass(path, db, seconds=8):
    """A 125 Hz tone whose peak is `db` dBFS (ffmpeg's `sine` starts 18.06 dB down)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=frequency=125:duration={seconds},volume={db + 18.06:.2f}dB", "-ac", "2", str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def disc(tmp_path):
    make_bass(tmp_path / "music" / "a.flac", -12)
    make_bass(tmp_path / "music" / "b.flac", -20)
    scan = scan_folder(tmp_path / "music")
    with Library.open(":memory:") as lib:
        lib.replace_source("folder", str(tmp_path / "music"), "m", scan.tracks, scan.nodes)  # fmt: skip
        disc_id = lib.create_disc("Bass")
        lib.add_to_disc(disc_id, [t.id for t in lib.tracks(sort="title")])
        yield lib, disc_id


def test_the_plan_asks_for_a_waveform_only_for_tracks_that_have_none(disc):
    lib, disc_id = disc
    first, second = [e.track for e in lib.disc_entries(disc_id)]
    assert [t.wave_needed for t in plan_burn(lib, disc_id).tracks] == [True, True]
    lib.set_waveform(first.id, [5] * 100)
    assert [t.wave_needed for t in plan_burn(lib, disc_id).tracks] == [False, True]
    assert second.waveform is None


@needs_ffmpeg
def test_a_render_returns_the_waveform_of_each_track_that_needed_one(disc, tmp_path):
    lib, disc_id = disc
    first = lib.disc_entries(disc_id)[0].track
    lib.set_waveform(first.id, [5] * 100)
    rendered = render_plan(plan_burn(lib, disc_id), tmp_path / "out")
    second = lib.disc_entries(disc_id)[1].track
    assert set(rendered.waveforms) == {
        second.id
    }  # the first had one: not worked out again
    assert len(rendered.waveforms[second.id]) == 100 and max(rendered.waveforms[second.id]) == 15  # fmt: skip


@needs_ffmpeg
def test_the_waveform_does_not_depend_on_the_gain_the_burn_applied(disc, tmp_path):
    lib, disc_id = disc
    plan = plan_burn(lib, disc_id)
    quiet = BurnPlan(**{**plan.__dict__, "tracks": tuple(
        PlanTrack(t.track_id, t.path, t.title, t.artist, t.dir_id, gain_db=-9.0, wave_needed=True) for t in plan.tracks)})  # fmt: skip
    loud = render_plan(plan, tmp_path / "loud").waveforms
    turned_down = render_plan(quiet, tmp_path / "quiet").waveforms
    for track_id in loud:
        assert all(
            abs(a - b) <= 1 for a, b in zip(loud[track_id], turned_down[track_id])
        )


@needs_ffmpeg
def test_a_waveform_that_cannot_be_worked_out_never_fails_the_burn(disc, tmp_path, monkeypatch, caplog):  # fmt: skip
    lib, disc_id = disc

    def broken(_audio):
        raise ValueError("too short")

    monkeypatch.setattr(wave_model, "column_values", broken)
    rendered = render_plan(plan_burn(lib, disc_id), tmp_path / "out")
    assert rendered.waveforms == {} and len(rendered.lengths) == 2  # the burn goes on
    assert "no waveform" in caplog.text


@needs_ffmpeg
def test_after_a_burn_sync_finds_nothing_left_to_analyse(disc, tmp_path):
    lib, disc_id = disc
    assert (
        card_discs.prepare_disc(lib, disc_id, waves=True).request is None
    )  # (no lengths yet)
    rendered = render_plan(plan_burn(lib, disc_id), tmp_path / "out")
    remember_rendered(lib, rendered.lengths, rendered.waveforms)
    found = card_discs.prepare_disc(lib, disc_id, waves=True)
    assert found.to_analyse == []
    assert all(v is not None for v in found.request.waves.values())
    # a track that lost its waveform (or was never burned here) is the only one Sync still analyses
    gone = lib.disc_entries(disc_id)[0].track
    lib._conn.execute("UPDATE track SET waveform = NULL WHERE id = ?", (gone.id,))
    again = card_discs.prepare_disc(lib, disc_id, waves=True)
    assert [t.id for t in again.to_analyse] == [gone.id]


def test_a_file_that_is_not_there_gives_no_waveform_rather_than_an_error(tmp_path):
    assert build._waveform_of(tmp_path / "nothing.wav") is None
    cut = tmp_path / "cut.wav"
    cut.write_bytes(b"RIFF")
    assert build._waveform_of(cut) is None
