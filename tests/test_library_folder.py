import shutil
import subprocess

import pytest

from dubcrate.library import Library
from dubcrate.library.folder import find_audio_files, scan_folder

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg not installed",
)


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def make_flac(path, seconds=0.5, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"sine=duration={seconds}", *meta, str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_find_audio_files_filters_and_sorts(tmp_path):
    touch(tmp_path / "b.MP3")
    touch(tmp_path / "A.flac")
    touch(tmp_path / "notes.txt")
    touch(tmp_path / "._b.mp3")
    touch(tmp_path / ".hidden" / "secret.mp3")
    touch(tmp_path / "sub" / "deep" / "x.wav")
    touch(tmp_path / "emptydir" / "readme.md")
    found = find_audio_files(tmp_path)
    assert [p.name for p in found[tmp_path]] == ["A.flac", "b.MP3"]
    assert [p.name for p in found[tmp_path / "sub" / "deep"]] == ["x.wav"]
    assert set(found) == {tmp_path, tmp_path / "sub" / "deep"}


@needs_ffmpeg
def test_scan_reads_tags_and_builds_the_directory_tree(tmp_path):
    make_flac(tmp_path / "one.flac", title="One", artist="Me", genre="House", date="2024-05-01",
              bpm="128", initialkey="8A", comment="hi")  # fmt: skip
    make_flac(tmp_path / "Sets" / "Warm" / "two.flac")
    make_flac(tmp_path / "Sets" / "Warm" / "three.flac")
    (tmp_path / "Empty").mkdir()
    scan = scan_folder(tmp_path)
    assert not scan.errors
    by_name = {t.path.rsplit("/", 1)[1]: t for t in scan.tracks}
    one = by_name["one.flac"]
    assert (one.title, one.artist, one.genre, one.year) == ("One", "Me", "House", 2024)
    assert (one.bpm, one.key, one.comment) == (128.0, "8A", "hi")
    assert 0.4 < one.duration < 0.7 and one.file_size
    assert by_name["two.flac"].title == "two"  # untagged files fall back to the stem

    names = {n.name: n for n in scan.nodes}
    assert [n.name for n in scan.nodes] == [
        tmp_path.name,
        "Sets",
        "Warm",
    ]  # Empty is pruned
    assert names[tmp_path.name].parent_ext_id is None and names[tmp_path.name].is_folder
    assert names["Sets"].is_folder and not names["Sets"].track_ext_ids
    assert not names["Warm"].is_folder
    assert [p.rsplit("/", 1)[1] for p in names["Warm"].track_ext_ids] == [
        "three.flac",
        "two.flac",
    ]


@needs_ffmpeg
def test_unreadable_files_are_reported_not_fatal(tmp_path):
    make_flac(tmp_path / "good.flac")
    touch(tmp_path / "broken.mp3")
    scan = scan_folder(tmp_path)
    assert [t.title for t in scan.tracks] == ["good"]
    assert [p.rsplit("/", 1)[1] for p, _ in scan.errors] == ["broken.mp3"]
    assert all("broken.mp3" not in e for n in scan.nodes for e in n.track_ext_ids)


@needs_ffmpeg
def test_progress_reports_every_file(tmp_path):
    for name in "abc":
        make_flac(tmp_path / f"{name}.flac")
    seen = []
    scan_folder(tmp_path, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_scan_requires_a_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        scan_folder(tmp_path / "missing")


@needs_ffmpeg
def test_scan_round_trips_through_the_store(tmp_path):
    make_flac(tmp_path / "Crate" / "a.flac", title="A", artist="X")
    scan = scan_folder(tmp_path)
    with Library.open(":memory:") as lib:
        first = lib.replace_source(
            "folder", str(tmp_path), "t", scan.tracks, scan.nodes
        )
        again = lib.replace_source(
            "folder", str(tmp_path), "t", scan.tracks, scan.nodes
        )
        assert (first.added, again.added, again.updated) == (1, 0, 1)
        node = next(n for n in lib.nodes(first.source_id) if n.name == "Crate")
        assert [t.title for t in lib.tracks(node_id=node.id)] == ["A"]


@needs_ffmpeg
def test_a_corrupt_flac_that_probes_cleanly_is_still_unreadable(tmp_path):
    make_flac(tmp_path / "good.flac")
    touch(tmp_path / "corrupt.flac")  # ffprobe exits 0 for this but finds no audio
    scan = scan_folder(tmp_path)
    assert [t.title for t in scan.tracks] == ["good"]
    assert [p.rsplit("/", 1)[1] for p, _ in scan.errors] == ["corrupt.flac"]
