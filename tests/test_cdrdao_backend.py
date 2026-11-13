"""The cdrdao backend: reading the drive, reporting progress, explaining failures.

The failure messages are ones cdrdao 1.2.4 really printed (without a drive, in Docker); the
`disk-info` labels are from the strings in its binary. What a real drive prints is NOT checked,
so the stand-in `cdrdao` below only plays back what the parsers are written for."""

import sys

import pytest

from dubcrate import tools
from dubcrate.burn import BurnError, BurnOptions
from dubcrate.burn.base import Disc, DiscTrack
from dubcrate.burn.cdrdao import (
    MIN_TRACK_SECTORS,
    CdrdaoBurner,
    _ProgressFilter,
    explain_failure,
    parse_disk_info,
    scan_devices,
)

BLANK = """\
Cdrdao version 1.2.4 - (C) Andreas Mueller <andreas@daneb.de>
/dev/sr0: HL-DT-ST DVDRAM GH24NSC0  Rev: LY00
Using driver: Generic SCSI-3/MMC - Version 2.0 (options 0x0000)

That data below may not reflect the real status of the inserted medium
if a simulation run was performed before. Reload the medium in this case.

CD-RW                : no
Total Capacity       : 79:57:74 (359849 blocks, 702 MB/669 MiB)
CD-R medium          : Prodisc Technology Inc.
Recording Speed      : 4x - 48x
CD-R empty           : yes
Toc Type             : CD-DA or CD-ROM
Sessions             : 0
Appendable           : yes
"""

USED = BLANK.replace("CD-R empty           : yes", "CD-R empty           : no")


def test_a_blank_disc_is_read_from_disk_info():
    info = parse_disk_info(BLANK)
    assert info.drive == "HL-DT-ST DVDRAM GH24NSC0"
    assert (info.present, info.blank, info.free_sectors) == (True, True, 359_849)


def test_a_disc_that_is_not_empty_says_so_and_claims_no_free_space():
    info = parse_disk_info(USED)
    assert (info.present, info.blank, info.free_sectors) == (True, False, None)


def test_what_cdrdao_did_not_say_stays_unknown():
    info = parse_disk_info(
        "Cdrdao version 1.2.4\nERROR: Cannot setup device /dev/sr0.\n"
    )
    assert (info.drive, info.present, info.blank, info.free_sectors) == ("", None, None, None)  # fmt: skip


@pytest.mark.parametrize(
    ("output", "expect"),
    [
        # as cdrdao 1.2.4 printed them
        (
            "ERROR: Unable to open SCSI device /dev/sr0: No such file or directory.\nERROR: Cannot setup device /dev/sr0.",
            "no drive at /dev/sr0",
        ),
        (
            "ERROR: Unable to open SCSI device /dev/sr0: Permission denied.",
            "isn't allowed to use the drive /dev/sr0",
        ),
        (
            "ERROR: Unable to open SCSI device /dev/sr0: Permission denied.",
            "usermod -aG",
        ),
        (
            'WARNING: Track 1: Length is shorter than 4 seconds.\nERROR: Toc file "disc.toc" is inconsistent.',
            "shorter than 4 seconds",
        ),
        # from the strings in its binary
        ("Medium in recorder device is not empty and not appendable.", "isn't blank"),
        ("Please insert a recordable medium and hit enter.", "no usable blank disc"),
        (
            "ERROR: Writing speed 52 not supported by device.",
            "can't write at that speed",
        ),
        ("Writing failed - buffer under run?", "buffer under-run"),
        ("ERROR: something new\nERROR: the last thing", "cdrdao said: the last thing"),
    ],
)
def test_failures_are_explained_in_words(output, expect):
    assert expect.lower() in explain_failure(output, "/dev/sr0", 1).lower()


def test_an_unrecognised_failure_still_says_something():
    assert explain_failure("", None, 7) == "cdrdao failed (status 7)."


def test_progress_is_one_line_every_ten_percent_not_one_per_update():
    said = []
    progress = _ProgressFilter(said.append)
    progress.feed("Writing track 01 (mode AUDIO/AUDIO data block size 2352)...")
    for mb in range(701):
        progress.feed(f"Wrote {mb} of 700 MB (Buffer 98%).")
    progress.feed("")
    progress.feed("Writing finished successfully.")
    assert said[0].startswith("Writing track 01") and said[-1] == "Writing finished successfully."  # fmt: skip
    writes = [s for s in said if s.startswith("Writing: ")]
    assert writes == [f"Writing: {p}% ({p * 7} of 700 MB)" for p in range(10, 101, 10)]


# -- with a stand-in cdrdao ---------------------------------------------------------------

pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in is a shell script")  # fmt: skip

FAKE = """#!/bin/sh
# a stand-in for cdrdao: records its arguments, then plays back what the test asked for
echo "$@" >> "$FAKE_LOG"
case "$1" in
scanbus) echo "/dev/sr0 : TEST, DRIVE , 1.0" ;;
disk-info) cat "$FAKE_DIR/disk-info.txt"; exit "$(cat "$FAKE_DIR/rc" 2>/dev/null || echo 0)" ;;
write)
  # nothing may be waiting for a keypress: stdin must be closed
  if read -r _line; then echo "stdin was open" >> "$FAKE_LOG"; fi
  cat "$FAKE_DIR/write.txt"; exit "$(cat "$FAKE_DIR/rc" 2>/dev/null || echo 0)" ;;
esac
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    exe = bundle / "cdrdao"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    here = tmp_path / "fake"
    here.mkdir()
    monkeypatch.setenv("FAKE_DIR", str(here))
    monkeypatch.setenv("FAKE_LOG", str(here / "log"))
    monkeypatch.setattr(tools, "BUNDLE_DIR", bundle)

    class Fake:
        def say(self, name, text):
            (here / name).write_text(text)

        def calls(self):
            return (here / "log").read_text().splitlines() if (here / "log").exists() else []  # fmt: skip

    return Fake()


def disc(tmp_path, seconds=(10, 12)):
    return Disc(
        "D", "P",
        [DiscTrack(tmp_path / f"{n}.wav", f"T{n}", "P", s * 75) for n, s in enumerate(seconds, 1)],
    )  # fmt: skip


@pytestmark_posix
def test_media_info_asks_the_first_drive_and_reads_the_answer(fake):
    fake.say("disk-info.txt", BLANK)
    info = CdrdaoBurner().media_info(None)
    assert (info.drive, info.blank, info.free_sectors) == ("HL-DT-ST DVDRAM GH24NSC0", True, 359_849)  # fmt: skip
    assert fake.calls() == ["scanbus", "disk-info --device /dev/sr0"]
    assert scan_devices() == [("/dev/sr0", "TEST, DRIVE , 1.0")]


@pytestmark_posix
def test_media_info_with_no_disc_says_no_disc(fake):
    fake.say("disk-info.txt", "ERROR: Cannot determine status of inserted medium.\n")
    fake.say("rc", "1")
    assert CdrdaoBurner().media_info("/dev/sr0").present is False


@pytestmark_posix
def test_media_info_failure_is_an_expected_error_with_a_fix(fake):
    fake.say("disk-info.txt", "ERROR: Unable to open SCSI device /dev/sr0: Permission denied.\n")  # fmt: skip
    fake.say("rc", "1")
    with pytest.raises(BurnError, match="isn't allowed to use the drive /dev/sr0"):
        CdrdaoBurner().media_info("/dev/sr0")


@pytestmark_posix
def test_a_burn_runs_cdrdao_with_the_options_and_a_closed_stdin(fake, tmp_path):
    fake.say("write.txt", "Writing track 01 (mode AUDIO/AUDIO)...\nWrote 350 of 700 MB (Buffer 99%).\nWriting finished successfully.\n")  # fmt: skip
    said = []
    CdrdaoBurner().burn(
        disc(tmp_path),
        BurnOptions(speed=12, simulate=True, eject=False, device="/dev/sr1"),
        said.append,
    )
    (write,) = [c for c in fake.calls() if c.startswith("write")]
    assert write.startswith("write --device /dev/sr1 -n --speed 12 --simulate ")
    assert write.endswith("disc.toc") and "--eject" not in write
    assert "stdin was open" not in fake.calls()
    assert "Writing: 50% (350 of 700 MB)" in said and said[-1] == "Writing finished successfully."  # fmt: skip


@pytestmark_posix
def test_a_failed_burn_says_why_in_words(fake, tmp_path):
    fake.say(
        "write.txt", "Medium in recorder device is not empty and not appendable.\n"
    )
    fake.say("rc", "1")
    with pytest.raises(BurnError, match="isn't blank"):
        CdrdaoBurner().burn(disc(tmp_path), BurnOptions(device="/dev/sr0"), print)


@pytestmark_posix
def test_a_track_under_four_seconds_is_refused_before_cdrdao_is_run(fake, tmp_path):
    with pytest.raises(BurnError, match=r"Track 2 “T2” is shorter than 4 seconds"):
        CdrdaoBurner().burn(disc(tmp_path, seconds=(10, 3)), BurnOptions(device="/dev/sr0"), print)  # fmt: skip
    assert not any(c.startswith("write") for c in fake.calls())
    assert MIN_TRACK_SECTORS == 300


@pytestmark_posix
def test_no_drive_found_says_what_to_check(fake, tmp_path):
    (tmp_path / "bundle" / "cdrdao").write_text(
        "#!/bin/sh\nexit 0\n"
    )  # scanbus finds nothing
    with pytest.raises(BurnError, match="found no drives.*cdrom"):
        CdrdaoBurner().burn(disc(tmp_path), BurnOptions(), print)
