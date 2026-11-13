"""Dubcrate discs as card sync requests (no ffmpeg, no rekordbox: lengths are set, analysis is stood in)."""

from pathlib import Path

import pytest

from dubcrate import card_discs, card_sync
from dubcrate import cdj_card as cc
from dubcrate.analysis import Analysis, Cue
from dubcrate.library import Library, TrackRecord

DATA = Path(__file__).parent / "data" / "cdj_card"


def cd2():
    return cc.toc_from_drutil((DATA / "CD2_TOC.txt").read_text(encoding="utf-8"))


def lengths_of(starts, lead_out):
    return [b - a for a, b in zip(starts, [*starts[1:], lead_out], strict=True)]


def rec(ext_id, **kw):
    return TrackRecord(
        ext_id=ext_id, path=f"/nonexistent/{ext_id}.flac", title=ext_id, **kw
    )


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def make_disc(lib, lengths, *, kind="audio", pregap=0, source="folder", **rec_kw):
    """A disc whose tracks have these measured lengths (sectors)."""
    lib.replace_source(
        source, f"/{source}{len(lib.sources())}", "S",
        [rec(f"{source}{len(lib.sources())}-{n}", duration=n + 1.0, **rec_kw) for n in range(len(lengths))],
    )  # fmt: skip
    ids = [t.id for t in lib.tracks(sort="path")][-len(lengths) :]
    for track_id, sectors in zip(ids, lengths, strict=True):
        lib.set_cd_sectors(track_id, sectors)
    disc_id = lib.create_disc("D", pregap_sectors=pregap, kind=kind)
    lib.add_to_disc(disc_id, ids)
    return disc_id


def cd2_disc(lib, **kw):
    return make_disc(lib, lengths_of(*cd2()), **kw)


def test_a_disc_of_measured_tracks_gives_the_id_of_its_toc(lib):
    result = card_discs.prepare_disc(lib, cd2_disc(lib))
    assert result.problems == []
    # the lengths are CD 2's, so the ID is the one the player made for CD 2
    assert result.request.disc_id == cc.DiscId.from_toc(*cd2())


def test_a_disc_not_marked_as_burned_says_so(lib):
    disc_id = cd2_disc(lib)
    assert any(
        "isn't marked as burned" in w
        for w in card_discs.prepare_disc(lib, disc_id).warnings
    )
    lib.mark_burned(disc_id)
    assert card_discs.prepare_disc(lib, disc_id).warnings == []


def test_a_disc_with_gaps_is_warned_about_not_refused(lib):
    disc_id = cd2_disc(lib, pregap=150)
    lib.mark_burned(disc_id)
    result = card_discs.prepare_disc(lib, disc_id)
    assert result.request is not None
    assert any("gaps between tracks" in w for w in result.warnings)
    # and the gaps are in the TOC it computed: each later track starts 150 frames further on
    starts, _ = card_discs.disc_toc(lib.disc_entries(disc_id), 150)
    assert starts[1] - starts[0] == lengths_of(*cd2())[0] + 150


def test_only_audio_discs_with_two_tracks_and_measured_lengths_can_be_synced(lib):
    mp3_disc = make_disc(lib, [1000, 1000, 1000], kind="mp3")
    assert "MP3 discs" in card_discs.prepare_disc(lib, mp3_disc).problems[0]

    one = make_disc(lib, [5000])
    assert "one track" in card_discs.prepare_disc(lib, one).problems[0]

    unmeasured = cd2_disc(lib)
    first = lib.disc_entries(unmeasured)[0].track.id
    lib._conn.execute("UPDATE track SET cd_sectors = NULL WHERE id = ?", (first,))
    result = card_discs.prepare_disc(lib, unmeasured)
    assert "1 of 12 tracks have no measured length" in result.problems[0]
    assert result.request is None


def test_rekordbox_cues_of_each_track_become_cues_at_the_discs_positions(
    lib, monkeypatch
):
    disc_id = cd2_disc(lib)
    entries = lib.disc_entries(disc_id)
    by_track = {
        entries[0].track.id: [Cue("memory", 15.0), Cue("hot", 40.0, slot=2)],
        entries[11].track.id: [Cue("loop", 30.0, end=34.0)],
    }
    monkeypatch.setattr(
        card_discs, "rekordbox_cues", lambda _lib, track: by_track.get(track.id, ())
    )
    result = card_discs.prepare_disc(lib, disc_id)
    starts, _ = cd2()
    assert result.cue_tracks == 2
    assert result.request.wanted == [
        card_sync.WantedCue(1, 150 + 15 * 75),
        card_sync.WantedCue(1, 150 + 40 * 75),
        card_sync.WantedCue(12, starts[11] + 30 * 75, end=starts[11] + 34 * 75),
    ]


def test_cues_come_from_rekordbox_through_the_analysis_files(lib, monkeypatch):
    lib.replace_source(
        "rekordbox", "/rb/master.db", "rb",
        [rec("rb1", analysis_path="PIONEER/USBANLZ/x/ANLZ0000", duration=100.0)],
    )  # fmt: skip
    track = lib.tracks()[0]
    seen = {}

    def fake(root, analysis_path, track_end):
        seen.update(root=root, path=analysis_path, end=track_end)
        return Analysis(cues=(Cue("memory", 12.5),))

    monkeypatch.setattr(card_discs, "read_analysis", fake)
    assert card_discs.rekordbox_cues(lib, track) == (Cue("memory", 12.5),)
    assert seen["path"] == "PIONEER/USBANLZ/x/ANLZ0000" and seen["end"] == 100.0
    assert seen["root"] == card_discs.share_root(Path("/rb/master.db"))


def test_a_file_in_a_folder_that_is_also_in_rekordbox_uses_rekordbox_cues(
    lib, monkeypatch
):
    shared = "/music/same.flac"
    lib.replace_source("rekordbox", "/rb/master.db", "rb", [
        TrackRecord(ext_id="rb1", path=shared, title="t", analysis_path="A/B", duration=60.0)
    ])  # fmt: skip
    lib.replace_source("folder", "/music", "music", [
        TrackRecord(ext_id=shared, path=shared, title="t", duration=60.0)
    ])  # fmt: skip
    folder_track = next(t for t in lib.tracks() if t.analysis_path is None)
    monkeypatch.setattr(
        card_discs,
        "read_analysis",
        lambda *a: Analysis(cues=(Cue("hot", 5.0, slot=1),)),
    )
    assert card_discs.rekordbox_cues(lib, folder_track) == (Cue("hot", 5.0, slot=1),)


def test_a_track_with_no_analysis_has_no_cues(lib, monkeypatch):
    disc_id = cd2_disc(lib)
    monkeypatch.setattr(
        card_discs, "read_analysis", lambda *a: pytest.fail("not asked")
    )
    result = card_discs.prepare_disc(lib, disc_id)
    assert result.request.wanted == [] and result.cue_tracks == 0
