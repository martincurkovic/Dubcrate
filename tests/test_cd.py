import pytest

from dubcrate.cd import (
    DISC_74_SECTORS,
    DISC_80_SECTORS,
    cdtext_clean,
    cdtext_toc_escape,
    format_msf,
    samples_to_sectors,
)


def test_disc_capacities():
    assert DISC_74_SECTORS == 333_000
    assert DISC_80_SECTORS == 360_000


@pytest.mark.parametrize(
    ("samples", "sectors"),
    [(0, 0), (1, 1), (588, 1), (589, 2), (44_100, 75), (44_100 * 60, 4500)],
)
def test_samples_to_sectors(samples, sectors):
    assert samples_to_sectors(samples) == sectors


def test_format_msf():
    assert format_msf(0) == "00:00:00"
    assert format_msf(75) == "00:01:00"
    assert format_msf(DISC_80_SECTORS) == "80:00:00"
    assert format_msf(76) == "00:01:01"


def test_cdtext_clean_keeps_latin1():
    assert cdtext_clean("Café Ünïcode") == "Café Ünïcode"


def test_cdtext_clean_folds_and_replaces():
    assert cdtext_clean("Łódź") == "?ódz"  # Ł has no decomposition; ź -> z
    assert cdtext_clean("★ Star") == "? Star"
    assert cdtext_clean("Ǎ") == "A"  # decomposes to A + combining caron


def test_cdtext_clean_strips_quotes_controls_and_whitespace():
    assert cdtext_clean('He said "hi"\n\tthere') == "He said hi there"


def test_cdtext_clean_truncates():
    assert len(cdtext_clean("x" * 500)) == 80


def test_toc_escape():
    assert cdtext_toc_escape("abc") == "abc"
    assert cdtext_toc_escape('a"b\\c') == "a\\042b\\134c"
    assert cdtext_toc_escape("é") == "\\351"
