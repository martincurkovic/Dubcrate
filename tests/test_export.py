"""Exporting a disc as files any burning program can write."""

import shutil
import subprocess
from pathlib import Path

import pytest

from dubcrate import export
from dubcrate.build import BuildError, plan_burn
from dubcrate.burn import cdrdao
from dubcrate.burn.base import Disc, DiscTrack
from dubcrate.cd import cdtext_toc_escape
from dubcrate.export import ExportError, export_plan, new_export_folder, wav_name
from dubcrate.iso import find_iso_tool
from dubcrate.library import Library
from dubcrate.library.folder import scan_folder

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def make_flac(path, seconds, **tags):
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
    make_flac(music / "b.flac", 3.0, title="Café Beta / Remix", artist="Zoë")
    scan = scan_folder(music)
    with Library.open(":memory:") as library:
        library.replace_source("folder", str(music), "music", scan.tracks, scan.nodes)
        yield library


def ids(lib):
    return [t.id for t in lib.tracks(sort="title")]


# -- names and folders (no ffmpeg) ---------------------------------------------------------


def test_wav_names_are_numbered_ascii_and_free_of_unsafe_characters():
    assert wav_name(1, "Zoë", "Café Beta / Remix") == "01 Zoe - Cafe Beta Remix.wav"
    assert wav_name(12, "", "日本語") == "12 Track.wav"  # nothing left of it
    assert wav_name(3, "A", "B").isascii()


def test_an_export_goes_in_a_new_folder_and_never_overwrites(tmp_path):
    first = new_export_folder(tmp_path, "Friday: Night/Set")
    assert first == tmp_path / "Friday Night Set export"
    (first / "keep.txt").write_text("mine")
    second = new_export_folder(tmp_path, "Friday: Night/Set")
    assert second.name.endswith("export 2")
    assert (first / "keep.txt").read_text() == "mine"


def test_exporting_into_a_folder_that_is_not_there_is_an_expected_error(tmp_path):
    with pytest.raises(ExportError, match="is not a folder"):
        new_export_folder(tmp_path / "nope", "Disc")


def test_the_toc_can_name_files_relative_to_where_it_is_written(tmp_path):
    disc = Disc("T", "P", [DiscTrack(tmp_path / "01 A.wav", "A", "P", 150)])
    relative = cdrdao.build_toc(disc, relative_to=tmp_path)
    assert 'FILE "01 A.wav" 0' in relative and str(tmp_path) not in relative
    assert cdtext_toc_escape(str(tmp_path.resolve())) in cdrdao.build_toc(
        disc
    )  # unchanged without it


# -- the real thing -------------------------------------------------------------------------


@needs_ffmpeg
def test_an_audio_disc_exports_wavs_a_toc_and_a_readme(lib, tmp_path):
    disc = lib.create_disc("Friday")
    lib.add_to_disc(disc, ids(lib))
    out = tmp_path / "out"
    out.mkdir()
    said = []
    result = export_plan(plan_burn(lib, disc), out, said.append)

    assert result.folder == out / "Friday export" and result.disc_folder is None
    names = sorted(p.name for p in result.folder.iterdir())
    assert names == ["01 Same - Alpha.wav", "02 Zoe - Cafe Beta Remix.wav", "README.txt", "disc.toc"]  # fmt: skip
    assert said  # progress was reported
    # each track is a whole number of sectors: 2 s and 3 s at 75 sectors a second
    assert sorted(result.lengths.values()) == [150, 225]
    assert (result.folder / "01 Same - Alpha.wav").stat().st_size >= 150 * 2352

    toc = result.toc.read_text(encoding="ascii")
    assert 'FILE "01 Same - Alpha.wav" 0' in toc and 'FILE "02 Zoe - Cafe Beta Remix.wav" 0' in toc  # fmt: skip
    assert 'TITLE "Friday"' in toc and 'TITLE "Alpha"' in toc  # the CD-TEXT is in it
    assert str(tmp_path) not in toc  # nothing absolute: the folder can be moved
    readme = result.readme.read_text(encoding="utf-8")
    assert "cdrdao write" in readme and "disc.toc" in readme and "2 tracks" in readme


@needs_ffmpeg
def test_a_second_export_does_not_touch_the_first(lib, tmp_path):
    disc = lib.create_disc("Friday")
    lib.add_to_disc(disc, ids(lib))
    plan = plan_burn(lib, disc)
    one = export_plan(plan, tmp_path)
    marker = one.folder / "mine.txt"
    marker.write_text("keep")
    two = export_plan(plan, tmp_path)
    assert two.folder != one.folder and marker.read_text() == "keep"


@needs_ffmpeg
def test_an_mp3_disc_exports_its_folder_with_the_readme_outside_it(lib, tmp_path):
    disc = lib.create_disc("Mixed Bag", kind="mp3")
    lib.add_to_disc(disc, ids(lib))
    result = export_plan(plan_burn(lib, disc), tmp_path)
    assert result.toc is None and result.lengths == {}
    assert result.disc_folder.parent == result.folder
    readme = result.readme.read_text(encoding="utf-8")
    if find_iso_tool():  # the image of the disc, next to its folder
        assert (
            result.iso == result.folder / "disc.iso" and result.iso.stat().st_size > 0
        )
        assert "disc.iso" in readme and "No disc.iso" not in readme
    else:
        assert result.iso is None and "No disc.iso was made" in readme
    files = sorted(p.name for p in result.disc_folder.iterdir())
    assert files == ["01 Same - Alpha.mp3", "02 Zo\u00eb - Caf\u00e9 Beta Remix.mp3"]  # fmt: skip
    assert not (
        result.disc_folder / "README.txt"
    ).exists()  # it must not end up on the disc
    assert "xorriso" in readme


@needs_ffmpeg
def test_a_failed_export_leaves_no_half_made_folder(lib, tmp_path, monkeypatch):
    disc = lib.create_disc("Friday")
    lib.add_to_disc(disc, ids(lib))
    plan = plan_burn(lib, disc)

    def broken(*_args, **_kwargs):
        raise BuildError("#2 Beta: ffmpeg failed")

    monkeypatch.setattr(export, "render_plan", broken)
    with pytest.raises(BuildError, match="ffmpeg failed"):
        export_plan(plan, tmp_path)
    assert list(tmp_path.glob("*export*")) == []


@needs_ffmpeg
def test_a_full_disk_is_explained_and_cleaned_up(lib, tmp_path, monkeypatch):
    disc = lib.create_disc("Friday")
    lib.add_to_disc(disc, ids(lib))
    plan = plan_burn(lib, disc)

    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(export, "render_plan", full)
    with pytest.raises(ExportError, match="No space left on device"):
        export_plan(plan, tmp_path)
    assert list(Path(tmp_path).glob("*export*")) == []
