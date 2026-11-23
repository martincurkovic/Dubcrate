"""`dubcrate cues timing`: the offset per track, read from the files' first frames."""

from mp3_frames import frame

from dubcrate import cli
from dubcrate import cue_timing as ct
from dubcrate.library import Library, TrackRecord


def make_disc(tmp_path, files: dict[str, bytes]):
    music = tmp_path / "music"
    music.mkdir()
    records = []
    for name, data in files.items():
        (music / name).write_bytes(data)
        records.append(
            TrackRecord(
                ext_id=name,
                path=str(music / name),
                title=name.split(".")[0],
                artist="A",
            )
        )
    with Library.open(tmp_path / "lib.db") as lib:
        lib.replace_source("folder", str(music), "M", records)
        disc = lib.create_disc("Set")
        lib.add_to_disc(disc, [t.id for t in lib.tracks(sort="path")])
        return disc


def test_each_track_shows_its_offset_group_and_certainty(tmp_path, capsys):
    disc = make_disc(
        tmp_path,
        {
            "a.mp3": frame(),  # LAME
            "b.mp3": frame(maker=b"Lavc"),  # ffmpeg
            "c.mp3": bytes([0xFF, 0xFB, 0x90, 0x00]) + bytes(400),  # no Info frame
            "d.mp3": frame(delay=0),  # left alone
            "e.wav": b"RIFF",
        },
    )
    assert (
        cli.main(["--library", str(tmp_path / "lib.db"), "cues", "timing", str(disc)])
        == 0
    )
    out = capsys.readouterr().out
    assert " 51 ms  lame" in out and " 25 ms  ffmpeg" in out
    assert "  0 ms  no-info" in out and "  0 ms  left-alone" in out and "not-mp3" in out
    assert out.count("(less certain)") == 2  # the ffmpeg one and the left-alone one
    assert "One of each to try on the player" in out
    assert "5 track(s)" not in out and "1 track(s): LAME files" in out


def test_an_unknown_disc_is_a_message(tmp_path, capsys):
    with Library.open(tmp_path / "lib.db"):
        pass
    assert (
        cli.main(["--library", str(tmp_path / "lib.db"), "cues", "timing", "99"]) == 1
    )
    assert "error:" in capsys.readouterr().err


def test_describe_says_which_group_and_how_sure(tmp_path):
    path = tmp_path / "x.mp3"
    path.write_bytes(frame())
    timing = ct.describe(path)
    assert (timing.lead_ms, timing.group, timing.sure) == (51, "lame", True)
    assert ct.describe(tmp_path / "missing.mp3").group == "unreadable"
