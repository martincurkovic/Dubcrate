# scripts/real_music.py (make_screenshots.py --real): the picks file, which tracks it can reach, the fallback, and the staged
# beat grid of the made-up collection. No Qt window, no rekordbox library, nothing of anyone's music.

import itertools
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import demo_music
import real_music
import screenshot_recipes
from real_music import PicksError, parse_picks

from dubcrate.library import TrackRecord
from dubcrate.library.rekordbox import RekordboxLibrary


def picks_data(**over):
    data = {
        "playlists": {
            "warm_up": {"name": "Warm Up", "tracks": ["Intro Tune"]},
            "house": {"name": "House", "tracks": ["One", "Two", "Three"]},
            "garage": {
                "name": "UK Garage",
                "tracks": [{"title": "Two", "artist": "B"}],
            },
            "throwbacks": {"name": "Peak Time", "tracks": ["One"]},
        },
        "loose": ["Three"],
        "show_art": ["Three"],
        "hero_tab": "info",
    }
    data.update(over)
    return data


def library(*specs):
    return RekordboxLibrary(
        source=Path("/x/master.db"),
        tracks=[
            TrackRecord(
                ext_id=str(i), path=f"/music/{i}.mp3", title=title, artist=artist
            )
            for i, (title, artist) in enumerate(specs)
        ],
    )


# -- the picks file ------------------------------------------------------------------------------


def test_a_picks_file_is_read_with_its_names_loose_tracks_and_art():
    picks = parse_picks(picks_data())
    assert [picks.playlists[r].name for r in real_music.ROLES] == ["Warm Up", "House", "UK Garage", "Peak Time"]  # fmt: skip
    assert [str(p) for p in picks.playlists["garage"].tracks] == ["B - Two"]
    assert [p.title for p in picks.loose] == ["Three"]
    assert picks.show_art == {"three"} and picks.hero_tab == "info" and picks.source == "Demo Crate"  # fmt: skip
    assert picks.library is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["playlists"].pop("garage"), "missing: garage"),
        (
            lambda d: d["playlists"]["house"].update(tracks=[]),
            "needs at least one track",
        ),
        (
            lambda d: d["playlists"]["house"].update(tracks=["a", "b"]),
            "at least three tracks",
        ),
        (lambda d: d["playlists"]["warm_up"].update(name=" "), 'needs a "name"'),
        (lambda d: d["playlists"]["warm_up"].update(tracks=[3]), "a track is a title"),
        (lambda d: d.update(hero_tab="cue"), "memory or info"),
        (lambda d: d.update(playlists=[]), 'needs a "playlists" object'),
    ],
)
def test_a_wrong_picks_file_says_what_is_wrong(change, message):
    data = picks_data()
    change(data)
    with pytest.raises(PicksError, match=message):
        parse_picks(data)


def test_a_file_that_is_not_json_is_an_error_not_a_fallback(tmp_path):
    bad = tmp_path / "picks.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(PicksError, match="not valid JSON"):
        real_music.status(bad)  # (the file is there: only a missing one falls back)


# -- which tracks it can reach -----------------------------------------------------------------------


def test_only_the_named_tracks_are_selected_in_the_picks_order_and_one_in_two_playlists_is_one_record():
    rb = library(
        ("One", "A"),
        ("Two", "B"),
        ("Three", "A"),
        ("Intro Tune", "A"),
        ("Not Named", "Z"),
    )
    picks = parse_picks(picks_data())
    records, nodes, loose = real_music.select(rb, picks)
    assert [r.title for r in records] == ["Intro Tune", "One", "Two", "Three"]  # (nothing else: "Not Named" can't leak)  # fmt: skip
    assert [n.name for n in nodes] == ["Warm Up", "House", "UK Garage", "Peak Time"]
    house = next(n for n in nodes if n.name == "House")
    assert [rb.tracks[int(i)].title for i in house.track_ext_ids] == [
        "One",
        "Two",
        "Three",
    ]
    assert next(n for n in nodes if n.name == "Peak Time").track_ext_ids == (
        "0",
    )  # (One again)
    assert [t.title for t in loose] == ["Three"]


def test_a_title_matches_without_regard_to_case_or_spacing():
    rb = library(("The  Track", "A"))
    picks = parse_picks(picks_data(playlists={
        "warm_up": {"name": "w", "tracks": ["the track"]},
        "house": {"name": "h", "tracks": ["THE TRACK", "the track", "The Track"]},
        "garage": {"name": "g", "tracks": ["the track"]},
        "throwbacks": {"name": "t", "tracks": ["the track"]},
    }, loose=[]))  # fmt: skip
    records, _nodes, _loose = real_music.select(rb, picks)
    assert len(records) == 1


def test_a_track_the_library_lacks_or_two_tracks_with_one_title_is_an_error():
    picks = parse_picks(picks_data())
    with pytest.raises(
        PicksError, match="“Intro Tune” is not in the rekordbox library"
    ):
        real_music.select(library(("One", "A"), ("Two", "B"), ("Three", "A")), picks)
    twins = library(
        ("Intro Tune", "A"), ("One", "A"), ("One", "B"), ("Two", "B"), ("Three", "A")
    )
    with pytest.raises(PicksError, match="matches 2 tracks \\(A; B\\): add an artist"):
        real_music.select(twins, picks)


def test_an_artist_picks_between_two_tracks_with_one_title():
    twins = library(
        ("Intro Tune", "A"), ("One", "A"), ("Two", "A"), ("Two", "B"), ("Three", "A")
    )
    data = picks_data()
    data["playlists"]["house"]["tracks"] = [
        "One",
        {"title": "Two", "artist": "A"},
        "Three",
    ]
    picks = parse_picks(data)  # ("garage" names Two by B)
    records, nodes, _loose = real_music.select(twins, picks)
    by_id = {r.ext_id: r for r in records}
    house = next(n for n in nodes if n.name == "House")
    garage = next(n for n in nodes if n.name == "UK Garage")
    assert [
        by_id[i].artist for i in house.track_ext_ids if by_id[i].title == "Two"
    ] == ["A"]
    assert [by_id[i].artist for i in garage.track_ext_ids] == ["B"]
    assert len(records) == 5  # (both Twos, once each)


# -- when real mode can't run here -------------------------------------------------------------------------


def test_with_no_picks_file_or_no_library_it_says_why_and_a_good_setup_says_nothing(
    tmp_path,
):
    missing = tmp_path / "none.json"
    assert "no picks file" in real_music.status(missing)
    picks = tmp_path / "picks.json"
    picks.write_text(
        json.dumps(picks_data(library=str(tmp_path / "master.db"))), encoding="utf-8"
    )
    assert "no rekordbox library" in real_music.status(picks)
    (tmp_path / "master.db").write_bytes(b"x")
    assert real_music.status(picks) is None


def test_the_picks_file_lives_in_a_folder_git_ignores():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "local/" in ignored
    assert real_music.DEFAULT_PICKS.parent.name == "local"


# -- a real folder for the Explorer ------------------------------------------------------------------


def test_an_explorer_folder_is_read_from_the_picks_and_checked_to_exist(tmp_path):
    folder = tmp_path / "CDJ CDs"
    (folder / "Speed Garage" / "003").mkdir(parents=True)
    data = picks_data(explorer={"folder": str(folder), "pinned": "/Speed Garage/003/"})
    picks = parse_picks(data)
    assert (
        picks.explorer.folder == folder and picks.explorer.pinned == "Speed Garage/003"
    )
    assert parse_picks(picks_data()).explorer is None
    path = tmp_path / "picks.json"
    library_file = tmp_path / "master.db"
    library_file.write_bytes(b"x")
    path.write_text(json.dumps(picks_data(library=str(library_file), explorer={"folder": str(folder), "pinned": "Speed Garage/003"})), encoding="utf-8")  # fmt: skip
    assert real_music.status(path) is None
    path.write_text(json.dumps(picks_data(library=str(library_file), explorer={"folder": str(folder), "pinned": "Nope"})), encoding="utf-8")  # fmt: skip
    assert "there is no folder" in real_music.status(
        path
    )  # (fall back: this is not the owner's computer)


@pytest.mark.parametrize(
    "explorer",
    [{"folder": "/x"}, {"pinned": "a"}, {"folder": " ", "pinned": "a"}, "folder", 3],
)
def test_a_wrong_explorer_entry_is_an_error(explorer):
    with pytest.raises(PicksError, match='"explorer" needs'):
        parse_picks(picks_data(explorer=explorer))


def test_a_linked_folder_is_removed_as_a_link_and_its_files_stay(tmp_path):
    import shutil

    real = tmp_path / "CDJ CDs"
    (real / "A").mkdir(parents=True)
    (real / "A" / "one.mp3").write_bytes(b"audio")
    scratch = tmp_path / "scratch"
    real_music.link_folder(scratch / "home" / "Music" / "CDJ CDs", real)
    assert (
        scratch / "home" / "Music" / "CDJ CDs" / "A" / "one.mp3"
    ).read_bytes() == b"audio"
    real_music.link_folder(
        scratch / "home" / "Music" / "CDJ CDs", real
    )  # (again: nothing changes)
    shutil.rmtree(scratch)
    assert (
        real / "A" / "one.mp3"
    ).read_bytes() == b"audio"  # (rmtree unlinked the link and went no further)


def test_previews_are_worked_out_from_the_audio_files_in_the_folder_only(tmp_path):
    import math
    import struct
    import wave

    def tone(path, seconds=2.0):
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(44100)
            f.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 110 * i / 44100))) for i in range(int(44100 * seconds))))  # fmt: skip

    folder = tmp_path / "cd"
    folder.mkdir()
    tone(folder / "a.wav")
    (folder / "notes.txt").write_text("not audio", encoding="utf-8")
    (folder / "broken.mp3").write_bytes(b"not an mp3")
    (folder / "sub").mkdir()
    tone(folder / "sub" / "deeper.wav")
    found = real_music.audio_previews(folder)
    import os

    key = os.path.realpath(folder / "a.wav")
    assert list(found) == [key]  # (not the text, the broken file or the sub-folder's)
    assert found[key] is not None


# -- the Explorer's pretend folders ---------------------------------------------------------------------


def test_the_explorers_files_are_links_to_the_real_ones_never_copies(tmp_path):
    real = tmp_path / "music" / "a.mp3"
    real.parent.mkdir()
    real.write_bytes(b"audio")
    track = TrackRecord(ext_id="1", path=str(real), title="A")
    folder = tmp_path / "home" / "Loose"
    real_music.link_files(folder, [track])
    link = folder / "a.mp3"
    assert link.is_symlink() and link.resolve() == real.resolve()
    real_music.link_files(folder, [track])  # (again: nothing changes, nothing fails)
    assert real.read_bytes() == b"audio"
    # clearing the scratch folder leaves the music alone
    import shutil

    shutil.rmtree(tmp_path / "home")
    assert real.read_bytes() == b"audio"


# -- a cover picture only for a track the picks file allows ----------------------------------------------


class Strip:
    def __init__(self):
        self.clicked = []
        self.side = type("S", (), {"tabs": [self._tab(i) for i in range(3)]})()

    def _tab(self, i):
        outer = self

        class Tab:
            def click(self):
                outer.clicked.append(i)

        return Tab()


def hero(real, tab, title, allowed=("flowers (extended mix)",)):
    strip = Strip()
    window = type("W", (), {})()
    window.player_strip = strip
    window.preview = type("P", (), {"track": type("T", (), {"title": title})()})()
    ctx = type("C", (), {})()
    ctx.demo = type(
        "D", (), {"real": real, "hero_tab": tab, "show_art": frozenset(allowed)}
    )()
    ctx.settle = lambda *_a: None
    screenshot_recipes._hero_tab(ctx, window)
    return strip.clicked


def test_the_info_page_is_shown_only_for_an_allowed_track_in_a_real_collection():
    assert hero(True, "info", "Flowers (Extended Mix)") == [2]
    assert hero(True, "info", "  FLOWERS (extended   mix) ") == [2]
    assert (
        hero(True, "info", "Go Silly (Extended Mix)") == []
    )  # (its cover is not allowed)
    assert hero(True, "memory", "Flowers (Extended Mix)") == []
    assert (
        hero(False, "info", "Flowers (Extended Mix)") == []
    )  # (the made-up collection has none)


# -- the staged beat grid of the made-up collection ------------------------------------------------------


def test_the_staged_grid_is_every_beat_numbered_in_bars_from_zero():
    times, numbers, _phrases = demo_music.grid_for(128.0, 200.0)
    assert times[0] == 0.0 and abs(times[1] - 60 / 128) < 1e-9
    assert list(numbers[:9]) == [1, 2, 3, 4, 1, 2, 3, 4, 1]
    assert len(times) % 4 == 0 and times[-1] < 200.0


def test_the_staged_phrases_run_end_to_end_over_whole_bars_from_intro_to_outro():
    times, _numbers, phrases = demo_music.grid_for(124.0, 240.0)
    bar = 4 * 60 / 124
    assert phrases[0].label == "Intro" and phrases[-1].label == "Outro"
    assert phrases[0].start == 0.0
    for before, after in itertools.pairwise(phrases):
        assert before.end == after.start
        assert before.start < before.end
    assert abs(phrases[-1].end - len(times) / 4 * bar) < 1e-6
    assert all(abs((p.start / bar) - round(p.start / bar)) < 1e-6 for p in phrases)


def test_a_short_track_still_gets_a_grid_and_phrases():
    times, _numbers, phrases = demo_music.grid_for(140.0, 20.0)
    assert (
        len(times) >= 4 * 32 and phrases
    )  # (a track is at least 32 bars in the made-up collection)
