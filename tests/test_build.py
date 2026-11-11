import shutil
import subprocess
import wave

import pytest

from dubcrate import cli
from dubcrate.build import BuildError, build_burn_disc, disc_problems
from dubcrate.cd import SAMPLES_PER_SECTOR
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder
from dubcrate.library.lengths import measure_tracks

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def make_flac(path, seconds=2.0, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def lib(tmp_path):
    music = tmp_path / "music"
    make_flac(music / "a.flac", 2.0, title="Alpha", artist="Same")
    make_flac(music / "b.flac", 3.0, title="Beta", artist="Same")
    make_flac(music / "c.flac", 1.5, title="Gamma", artist="Other")
    scan = scan_folder(music)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
        yield library


def all_ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


def test_measure_fills_in_exact_lengths_once(lib):
    ids = all_ids(lib)
    assert all(t.cd_sectors is None for t in lib.tracks())
    assert measure_tracks(lib, ids) == []
    got = {t.title: t.cd_sectors for t in lib.tracks()}
    assert got == {"Alpha": 150, "Beta": 225, "Gamma": 113}  # 2.0 s, 3.0 s, 1.5 s
    lib.set_cd_sectors(ids[0], 999)  # already measured: left alone
    measure_tracks(lib, ids)
    assert lib.track(ids[0]).cd_sectors == 999


def test_measure_reports_unreadable_files_and_leaves_them_unmeasured(lib, tmp_path):
    [alpha] = lib.tracks(search="Alpha")
    (tmp_path / "music" / "a.flac").write_bytes(b"not audio")
    problems = measure_tracks(lib, [alpha.id])
    assert len(problems) == 1 and "a.flac" in problems[0]
    assert lib.track(alpha.id).cd_sectors is None


def test_measure_skips_tracks_whose_file_is_missing(lib, tmp_path):
    music = tmp_path / "music"
    scan = scan_folder(music)
    (music / "b.flac").unlink()
    lib.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
    [beta] = lib.tracks(search="Beta")
    assert beta.missing
    assert measure_tracks(lib, [beta.id]) == []
    assert lib.track(beta.id).cd_sectors is None  # keeps its conservative estimate


def test_build_renders_sector_aligned_wavs_and_records_exact_lengths(lib, tmp_path):
    disc_id = lib.create_disc("Night")
    lib.add_to_disc(disc_id, all_ids(lib))
    messages = []
    burn_disc, pregap = build_burn_disc(
        lib, disc_id, tmp_path / "work", progress=messages.append
    )
    assert pregap == 0 and burn_disc.title == "Night"
    assert [t.title for t in burn_disc.tracks] == ["Alpha", "Beta", "Gamma"]
    assert len(messages) == 3
    for t in burn_disc.tracks:
        with wave.open(str(t.wav)) as w:
            assert w.getnframes() == t.sectors * SAMPLES_PER_SECTOR
    # Rendered lengths are saved back as the authoritative ones.
    assert [e.track.cd_sectors for e in lib.disc_entries(disc_id)] == [
        t.sectors for t in burn_disc.tracks
    ]


def test_performer_is_the_common_artist_or_various(lib, tmp_path):
    same = lib.create_disc("Same")
    lib.add_to_disc(same, [t.id for t in lib.tracks(search="Same")])
    disc, _ = build_burn_disc(lib, same, tmp_path / "w1")
    assert disc.performer == "Same"
    mixed = lib.create_disc("Mixed")
    lib.add_to_disc(mixed, all_ids(lib))
    disc, _ = build_burn_disc(lib, mixed, tmp_path / "w2")
    assert disc.performer == "Various Artists"


def test_pregap_is_passed_through(lib, tmp_path):
    disc_id = lib.create_disc("Gaps", pregap_sectors=150)
    lib.add_to_disc(disc_id, all_ids(lib)[:2])
    _, pregap = build_burn_disc(lib, disc_id, tmp_path / "w")
    assert pregap == 150


def test_an_empty_or_overfull_disc_is_refused_before_rendering(lib, tmp_path):
    empty = lib.create_disc("Empty")
    assert disc_problems(lib, empty) == ["the disc is empty"]
    with pytest.raises(BuildError, match="empty"):
        build_burn_disc(lib, empty, tmp_path / "w")
    tiny = lib.create_disc("Tiny", capacity_sectors=200)
    lib.add_to_disc(tiny, all_ids(lib))
    with pytest.raises(BuildError, match="over capacity"):
        build_burn_disc(lib, tiny, tmp_path / "w")
    assert not (tmp_path / "w").exists()


def test_a_missing_file_is_a_problem(lib, tmp_path):
    disc_id = lib.create_disc("D")
    ids = all_ids(lib)
    lib.add_to_disc(disc_id, ids)
    [source] = lib.sources()
    (tmp_path / "music" / "b.flac").unlink()
    scan = scan_folder(tmp_path / "music")
    lib.replace_source("folder", source.path, "music", scan.tracks, scan.nodes)
    problems = disc_problems(lib, disc_id)
    assert any("no longer in its source" in p for p in problems)


# -- CLI wiring ---------------------------------------------------------------


def run_cli(tmp_path, *args):
    return cli.main(["--library", str(tmp_path / "lib.db"), *args])


def test_cli_plan_and_burn_dry_run(tmp_path, capsys, monkeypatch):
    music = tmp_path / "music"
    for name, secs in [("one", 2), ("two", 2)]:
        make_flac(music / f"{name}.flac", secs, title=name, artist="X")
    assert run_cli(tmp_path, "import-folder", str(music)) == 0
    assert run_cli(tmp_path, "disc", "new", "CLI disc", "--minutes", "74") == 0
    assert run_cli(tmp_path, "disc", "add", "1", "--node", "1") == 0
    out = capsys.readouterr().out
    assert 'Disc 1 "CLI disc": 74 min, gapless' in out and "2 tracks" in out

    def explode(_name):
        raise AssertionError("a dry run must never construct a burner")

    monkeypatch.setattr("dubcrate.cli_discs.get_burner", explode)
    assert run_cli(tmp_path, "burn", "1") == 0
    assert "dry run" in capsys.readouterr().out


def test_cli_burn_refuses_an_overfull_disc(tmp_path, capsys):
    music = tmp_path / "music"
    make_flac(music / "one.flac", 2, title="one")
    run_cli(tmp_path, "import-folder", str(music))
    run_cli(tmp_path, "disc", "new", "D")
    run_cli(tmp_path, "disc", "add", "1", "--node", "1")
    run_cli(tmp_path, "disc", "set", "1", "--minutes", "74")
    # Shrink it past what the track needs via the library directly.
    with Library.open(tmp_path / "lib.db") as lib:
        lib.update_disc(1, capacity_sectors=10)
    capsys.readouterr()
    assert run_cli(tmp_path, "burn", "1", "--yes") == 2
    assert "over capacity" in capsys.readouterr().err
