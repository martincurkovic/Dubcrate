from pathlib import Path

from dubcrate.burn.base import Disc, DiscTrack
from dubcrate.burn.cdrdao import build_toc


def _disc() -> Disc:
    return Disc(
        title="Warm Up",
        performer="DJ Test",
        tracks=[
            DiscTrack(Path("/tmp/a.wav"), "Intro", "Artist A", 1000),
            DiscTrack(Path("/tmp/b.wav"), "Café", "Artist B", 2000),
        ],
    )


def test_toc_structure():
    toc = build_toc(_disc())
    assert toc.startswith("CD_DA\nCD_TEXT {")
    assert toc.count("TRACK AUDIO") == 2
    assert 'TITLE "Warm Up"' in toc
    assert 'PERFORMER "DJ Test"' in toc
    assert f'FILE "{Path("/tmp/a.wav").resolve()}" 0' in toc
    assert 'TITLE "Caf\\351"' in toc


def test_toc_is_pure_ascii():
    toc = build_toc(_disc())
    toc.encode("ascii")


def test_disc_totals_and_fit():
    disc = _disc()
    assert disc.total_sectors == 3000
    assert disc.fits()
    disc.tracks.append(DiscTrack(Path("/tmp/c.wav"), "Long", "X", 400_000))
    assert not disc.fits()


def test_toc_is_gapless_by_default():
    assert "PREGAP" not in build_toc(_disc())


def test_toc_pregap_only_on_tracks_after_the_first():
    toc = build_toc(_disc(), pregap_sectors=150)
    assert toc.count("PREGAP 00:02:00") == 1
    assert toc.index("PREGAP") > toc.index('TITLE "Caf')  # after track 2 starts


def test_used_sectors_counts_gaps_between_tracks_only():
    disc = _disc()
    assert disc.used_sectors() == 3000
    assert disc.used_sectors(150) == 3150  # 2 tracks -> 1 gap
    assert Disc("x", "y").used_sectors(150) == 0


def test_fits_accounts_for_pregap():
    disc = _disc()
    disc.tracks[1] = DiscTrack(Path("/tmp/b.wav"), "Café", "Artist B", 359_000)
    assert disc.fits()
    assert not disc.fits(pregap_sectors=1500)
