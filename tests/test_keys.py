"""Musical keys in Camelot and in letters, and moving between them."""

import pytest

from dubcrate import keys

MINORS = ["G# min", "D# min", "A# min", "F min", "C min", "G min", "D min", "A min", "E min", "B min", "F# min", "C# min"]  # fmt: skip
MAJORS = ["B maj", "F# maj", "C# maj", "G# maj", "D# maj", "A# maj", "F maj", "C maj", "G maj", "D maj", "A maj", "E maj"]  # fmt: skip


def test_the_whole_wheel_is_right_both_ways():
    for number in range(1, 13):
        assert keys.display(f"{number}A", keys.MUSICAL) == MINORS[number - 1]
        assert keys.display(f"{number}B", keys.MUSICAL) == MAJORS[number - 1]
        assert keys.display(MINORS[number - 1], keys.CAMELOT) == f"{number}A"
        assert keys.display(MAJORS[number - 1], keys.CAMELOT) == f"{number}B"


@pytest.mark.parametrize(
    ("written", "camelot"),
    [
        ("8A", "8A"), ("08A", "8A"), ("8a", "8A"), ("12b", "12B"), (" 5B ", "5B"),
        ("Am", "8A"), ("A min", "8A"), ("A minor", "8A"), ("Amin", "8A"),
        ("Abm", "1A"), ("G#m", "1A"), ("G# min", "1A"), ("G♯m", "1A"), ("A♭m", "1A"),
        ("C", "8B"), ("C maj", "8B"), ("C major", "8B"), ("CM", "8B"),
        ("Db", "3B"), ("C#", "3B"), ("Db major", "3B"), ("F# major", "2B"), ("Gb", "2B"),
        ("Bbm", "3A"), ("Em", "9A"), ("F", "7B"), ("B", "1B"), ("Fm", "4A"), ("Ebm", "2A"),
        ("d#m", "2A"), ("e", "12B"),  # (lower-case letters are read too)
    ],
)  # fmt: skip
def test_keys_as_sources_write_them(written, camelot):
    assert keys.display(written, keys.CAMELOT) == camelot


@pytest.mark.parametrize(
    "text", ["", "n/a", "13A", "0A", "H", "Am7", "A sus", "8C", "key", "1"]
)
def test_what_is_not_a_key_is_left_as_it_was(text):
    assert keys.display(text, keys.CAMELOT) == text
    assert keys.display(text, keys.MUSICAL) == text
    assert keys.parse_key(text) is None


def test_every_key_survives_a_round_trip_in_either_notation():
    for number in range(1, 13):
        for letter in "AB":
            camelot = f"{number}{letter}"
            letters = keys.display(camelot, keys.MUSICAL)
            assert keys.parse_key(letters) == keys.parse_key(camelot)
            assert keys.display(letters, keys.CAMELOT) == camelot


def test_sharps_not_flats_in_letters():
    assert keys.display("Abm", keys.MUSICAL) == "G# min"
    assert keys.display("Db", keys.MUSICAL) == "C# maj"
    assert keys.display("Bb", keys.MUSICAL) == "A# maj"


def test_a_sort_goes_round_the_wheel_with_non_keys_last():
    written = ["10A", "Am", "1B", "", "n/a", "1A", "Fm", "8B", "12A", "2A"]
    ordered = sorted(written, key=keys.sort_value)
    assert ordered[:8] == [
        "1A",
        "1B",
        "2A",
        "Fm",
        "Am",
        "8B",
        "10A",
        "12A",
    ]  # 1A 1B 2A 4A 8A 8B 10A 12A
    assert set(ordered[8:]) == {"", "n/a"}
    assert keys.sort_value(None) == 100
