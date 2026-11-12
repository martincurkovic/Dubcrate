"""Reading the audio files directly inside one folder, for the Explorer."""

from pathlib import Path

import pytest

from dubcrate.library import folder
from dubcrate.library.folder import scan_directory
from dubcrate.render import RenderError, SourceInfo


@pytest.fixture
def probed(monkeypatch):
    """A stand-in for ffprobe that records what it was asked to read."""
    calls: list[str] = []

    def fake(path: Path):
        calls.append(path.name)
        if "broken" in path.name:
            raise RenderError("not audio")
        return SourceInfo(
            path=path,
            duration=61.0,
            sample_rate=44100,
            bits=16,
            title=path.stem.title(),
            artist="Someone",
            album="",
            bpm=128.0,
            key="8A",
        )

    monkeypatch.setattr(folder, "probe", fake)
    return calls


def touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_only_the_audio_files_directly_inside_are_read_in_name_order(tmp_path, probed):
    for name in (
        "b.mp3",
        "A.flac",
        "c.wav",
        "notes.txt",
        "cover.jpg",
        ".hidden.mp3",
        "._x.mp3",
    ):
        touch(tmp_path / name)
    touch(tmp_path / "sub" / "deep.mp3")
    scan = scan_directory(tmp_path)
    assert [Path(t.path).name for t in scan.tracks] == ["A.flac", "b.mp3", "c.wav"]
    assert scan.errors == [] and "deep.mp3" not in probed


def test_the_tags_and_size_come_through(tmp_path, probed):
    touch(tmp_path / "x.mp3", size=1234)
    [track] = scan_directory(tmp_path).tracks
    assert (track.title, track.artist, track.bpm, track.key) == (
        "X",
        "Someone",
        128.0,
        "8A",
    )
    assert track.file_size == 1234 and track.ext_id == str(tmp_path / "x.mp3")


def test_an_unreadable_file_is_reported_and_the_rest_still_read(tmp_path, probed):
    touch(tmp_path / "good.mp3")
    touch(tmp_path / "broken.mp3")
    scan = scan_directory(tmp_path)
    assert [Path(t.path).name for t in scan.tracks] == ["good.mp3"]
    [(path, why)] = scan.errors
    assert path.endswith("broken.mp3") and "not audio" in why


def test_a_file_that_is_unchanged_is_not_read_again(tmp_path, probed):
    touch(tmp_path / "same.mp3", size=10)
    touch(tmp_path / "grown.mp3", size=10)
    known = {t.path: t for t in scan_directory(tmp_path).tracks}
    probed.clear()
    touch(tmp_path / "grown.mp3", size=20)  # a different file now
    touch(tmp_path / "new.mp3")
    scan = scan_directory(tmp_path, known)
    assert sorted(probed) == ["grown.mp3", "new.mp3"]  # "same.mp3" kept what it had
    assert [Path(t.path).name for t in scan.tracks] == [
        "grown.mp3",
        "new.mp3",
        "same.mp3",
    ]


def test_an_empty_folder_has_no_tracks(tmp_path, probed):
    scan = scan_directory(tmp_path)
    assert scan.tracks == [] and scan.errors == []


def test_a_missing_folder_raises(tmp_path):
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        scan_directory(tmp_path / "nope")
