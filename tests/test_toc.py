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
