"""The check after a burn: the disc's real TOC against the plan, then the tray (the drive stood in for)."""

import pytest

from dubcrate import burn_check
from dubcrate import cdj_card as cc
from dubcrate.drive_toc import DriveTocError
from dubcrate.eject import EjectError

LENGTHS = [15000, 16000, 14000]


def planned(gap=0):
    return cc.toc_from_lengths(LENGTHS, gap)


class Rig:
    def __init__(self, toc=None, read_error=None, eject_error=None):
        self.said, self.ejected, self.reads = [], [], []
        self._toc, self._read_error, self._eject_error = toc, read_error, eject_error

    def read(self, **kw):
        self.reads.append(kw)
        if self._read_error:
            raise self._read_error
        return self._toc

    def eject(self):
        self.ejected.append(1)
        if self._eject_error:
            raise self._eject_error

    def run(self, lengths=LENGTHS, gap=0):
        return burn_check.check_burn(
            lengths,
            gap,
            self.said.append,
            read=self.read,
            eject=self.eject,
            sleep=lambda _s: None,
        )


def test_a_disc_whose_toc_is_as_planned_is_said_to_match_and_is_ejected():
    rig = Rig(toc=planned())
    assert rig.run() is True
    assert "exactly as planned" in rig.said[0] and "card" in rig.said[0]
    assert rig.ejected == [1]


def test_a_disc_with_gaps_is_compared_with_the_gaps_in_the_plan():
    assert Rig(toc=planned(150)).run(gap=150) is True
    assert (
        Rig(toc=planned(0)).run(gap=150) is False
    )  # (a gapless disc, planned with gaps)


def test_a_toc_that_differs_is_reported_with_what_differs_and_what_it_means():
    starts, lead_out = planned()
    rig = Rig(toc=([starts[0], starts[1] + 1, starts[2]], lead_out))
    assert rig.run() is False
    (said,) = rig.said
    assert "not what was planned" in said and "track 2 starts at" in said
    assert "The disc itself is fine" in said and "--from-drive" in said
    assert rig.ejected == [1]


def test_the_drive_is_asked_patiently_because_a_new_disc_takes_a_while():
    rig = Rig(toc=planned())
    rig.run()
    assert rig.reads == [
        {
            "sleep": rig.reads[0]["sleep"],
            "attempts": burn_check.ATTEMPTS,
            "wait": burn_check.WAIT_SECONDS,
        }
    ]


def test_a_disc_that_cant_be_read_is_said_and_still_ejected_and_nothing_is_raised():
    rig = Rig(
        read_error=DriveTocError("There's no readable disc in the drive (No media).")
    )
    assert rig.run() is None
    assert rig.said == [
        "Couldn't check the disc's table of contents: There's no readable disc in the drive (No media)."
    ]
    assert rig.ejected == [1]


def test_an_unexpected_error_in_the_check_never_fails_the_burn(caplog):
    rig = Rig(read_error=RuntimeError("something odd"))
    assert rig.run() is None  # (not raised)
    assert "see the log" in rig.said[0] and rig.ejected == [1]
    assert "checking the burned disc's TOC failed" in caplog.text


def test_a_tray_that_wont_open_is_said_and_not_raised():
    rig = Rig(
        toc=planned(), eject_error=EjectError("The system couldn't eject the disc.")
    )
    assert rig.run() is True
    assert (
        rig.said[-1] == "The system couldn't eject the disc. Eject the disc yourself."
    )
    odd = Rig(toc=planned(), eject_error=RuntimeError("odd"))
    odd.run()
    assert "eject it yourself" in odd.said[-1]


@pytest.mark.parametrize("lengths", [[], [5000]])
def test_a_disc_too_short_to_have_a_toc_to_compare_is_still_ejected(lengths):
    rig = Rig(toc=planned())
    rig.run(lengths=lengths)
    assert rig.ejected == [1]
