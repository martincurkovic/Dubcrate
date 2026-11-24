"""The cdrdao backend: reading the drive, reporting progress, explaining failures.

The failure messages are ones cdrdao 1.2.4 really printed (without a drive, in Docker); the
`disk-info` labels are from the strings in its binary. What a real drive prints is NOT checked,
so the stand-in `cdrdao` below only plays back what the parsers are written for."""

import sys
from pathlib import Path

import pytest

from dubcrate import tools
from dubcrate.burn import BurnError, BurnOptions
from dubcrate.burn.base import Disc, DiscTrack
from dubcrate.burn.cdrdao import (
    MIN_TRACK_SECTORS,
    CdrdaoBurner,
    _ProgressFilter,
    cygwin_path,
    explain_failure,
    parse_disk_info,
    parse_max_write_speed,
    parse_scanbus,
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
drive-info) cat "$FAKE_DIR/drive-info.txt" 2>/dev/null; exit 0 ;;
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
    assert fake.calls() == ["scanbus", "disk-info --device /dev/sr0", "drive-info --device /dev/sr0"]  # fmt: skip
    assert scan_devices() == [("/dev/sr0", "TEST, DRIVE , 1.0")]


@pytestmark_posix
def test_media_info_with_no_disc_says_no_disc(fake):
    fake.say("disk-info.txt", "ERROR: Cannot determine status of inserted medium.\n")
    fake.say("rc", "1")
    assert CdrdaoBurner().media_info("/dev/sr0").present is False


@pytestmark_posix
def test_media_info_with_an_empty_tray_on_a_real_drive_says_no_disc(fake):
    # what cdrdao 1.2.6 printed on the LiteOn eSAU108 with the tray empty (2026-11-23)
    fake.say(
        "disk-info.txt",
        "/dev/sr0: slimtype eSAU108   3\tRev: HL09\nCD-TEXT writing is supported.\n"
        "Using driver: Generic SCSI-3/MMC - Version 2.0 (options 0x0010)\n\n"
        + "WARNING: Unit not ready, still trying...\n" * 10
        + "ERROR: Unit not ready, giving up.\nERROR: Cannot setup device /dev/sr0.\n",
    )
    fake.say("rc", "1")
    info = CdrdaoBurner().media_info("/dev/sr0")
    assert info.present is False  # not the raw "cdrdao said: Cannot setup device"


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
def test_a_drive_that_refuses_its_write_settings_is_tried_with_the_plain_mmc_driver(
    fake, tmp_path, monkeypatch
):
    monkeypatch.setattr("dubcrate.burn.cdrdao.RETRY_WAIT", 0)
    fake.say("write.txt", "ERROR: Cannot set write parameters mode page.\nERROR: Writing failed.\n")  # fmt: skip
    fake.say("rc", "1")
    said = []
    with pytest.raises(BurnError, match="Writing failed"):
        CdrdaoBurner().burn(disc(tmp_path), BurnOptions(device="/dev/sr0"), said.append)
    assert len([c for c in fake.calls() if c.startswith("write")]) == 2
    first, second = [c for c in fake.calls() if c.startswith("write")]
    assert "--driver" not in first and "--driver generic-mmc " in second
    assert any("plain MMC driver" in line for line in said)


@pytestmark_posix
def test_other_failures_are_not_retried(fake, tmp_path, monkeypatch):
    monkeypatch.setattr("dubcrate.burn.cdrdao.RETRY_WAIT", 0)
    fake.say("write.txt", "Medium in recorder device is not empty and not appendable.\n")  # fmt: skip
    fake.say("rc", "1")
    with pytest.raises(BurnError):
        CdrdaoBurner().burn(disc(tmp_path), BurnOptions(device="/dev/sr0"), print)
    assert len([c for c in fake.calls() if c.startswith("write")]) == 1


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


# -- permission to open the drive (Linux) -------------------------------------------------------------------


@pytest.fixture
def linux_drive(monkeypatch):
    """A pretend Linux: /dev/sr0 owned by group 24 (`cdrom`), and a user who is (or is not) allowed to open it.
    Stands in for `os` and `grp` as `cdrdao.access_problem` sees them; no real device is touched."""
    import os as real_os
    import types

    from dubcrate.burn import cdrdao

    state = types.SimpleNamespace(allowed=False, groups=[1000], members=[], exists=True)

    class FakeOs:
        R_OK, W_OK = real_os.R_OK, real_os.W_OK

        def __getattr__(self, name):
            return getattr(real_os, name)

        @staticmethod
        def stat(path):
            if not state.exists:
                raise FileNotFoundError(path)
            return types.SimpleNamespace(st_gid=24)

        @staticmethod
        def access(path, mode):
            return state.allowed

        @staticmethod
        def getgroups():
            return state.groups

    group = types.SimpleNamespace(
        getgrgid=lambda gid: types.SimpleNamespace(
            gr_name="cdrom", gr_mem=state.members
        )
    )
    monkeypatch.setitem(sys.modules, "grp", group)
    monkeypatch.setattr("getpass.getuser", lambda: "dj")
    monkeypatch.setattr(cdrdao, "os", FakeOs())
    monkeypatch.setattr(cdrdao.sys, "platform", "linux")
    return state


def test_a_drive_the_user_may_open_has_no_problem(linux_drive):
    from dubcrate.burn.cdrdao import access_problem

    linux_drive.allowed = True
    assert access_problem("/dev/sr0") is None


def test_a_drive_owned_by_a_group_the_user_is_not_in_says_which_and_how(linux_drive):
    from dubcrate.burn.cdrdao import access_problem

    text = access_problem("/dev/sr0")
    assert "“cdrom” group" in text and "you aren't in it" in text
    assert "sudo usermod -aG cdrom $USER" in text and "log out and in" in text and "udev" in text  # fmt: skip


def test_a_user_added_to_the_group_but_not_logged_in_again_is_told_to_log_out(
    linux_drive,
):
    from dubcrate.burn.cdrdao import access_problem

    linux_drive.members = [
        "dj"
    ]  # (listed in /etc/group, but this session's groups lack it)
    text = access_problem("/dev/sr0")
    assert "this session started before" in text and "usermod" not in text


def test_a_drive_that_is_not_there_is_left_to_cdrdaos_own_message(linux_drive):
    from dubcrate.burn.cdrdao import access_problem

    linux_drive.exists = False
    assert access_problem("/dev/sr9") is None


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_only_linux_checks_the_drive_group(linux_drive, monkeypatch, platform):
    from dubcrate.burn import cdrdao

    monkeypatch.setattr(cdrdao.sys, "platform", platform)
    assert cdrdao.access_problem("/dev/sr0") is None


def test_the_burner_refuses_before_running_cdrdao_when_it_may_not_open_the_drive(linux_drive, monkeypatch):  # fmt: skip
    from dubcrate.burn import cdrdao

    monkeypatch.setattr(cdrdao, "find_cdrdao", lambda: "/usr/bin/cdrdao")
    ran = []
    monkeypatch.setattr(cdrdao.subprocess, "run", lambda *a, **k: ran.append(a))
    burner = CdrdaoBurner()
    with pytest.raises(BurnError, match="“cdrom” group"):
        burner.media_info("/dev/sr0")
    with pytest.raises(BurnError, match="“cdrom” group"):
        burner.burn(
            Disc(title="t", performer="p"), BurnOptions(device="/dev/sr0"), print
        )
    assert ran == []  # (cdrdao was never started)


def test_a_permission_denied_from_cdrdao_names_the_group_when_it_can(linux_drive):
    text = explain_failure("ERROR: Unable to open SCSI device /dev/sr0: Permission denied.", "/dev/sr0", 1)  # fmt: skip
    assert "“cdrom” group" in text
    linux_drive.allowed = (
        True  # (the file is fine now: only the general advice is left)
    )
    assert "usermod -aG GROUP" in explain_failure("Permission denied", "/dev/sr0", 1)


DRIVE_INFO = """\
/dev/sr0: slimtype eSAU108   3	Rev: HL09
CD-TEXT writing is supported.
Using driver: Generic SCSI-3/MMC - Version 2.0 (options 0x0010)

Maximum reading speed: 4234 kB/s
Current reading speed: 4234 kB/s
Maximum writing speed: 4234 kB/s
Current writing speed: 4234 kB/s
BurnProof supported: yes
"""


def test_the_drives_top_writing_speed_is_read_in_x():
    assert (
        parse_max_write_speed(DRIVE_INFO) == 24
    )  # (a real drive's answer, 2026-11-24)
    assert parse_max_write_speed(DRIVE_INFO.replace("4234", "8467")) == 48
    assert parse_max_write_speed(DRIVE_INFO.replace("4234 kB/s", "0 kB/s")) is None
    assert parse_max_write_speed("nothing useful") is None


@pytestmark_posix
def test_media_info_carries_the_drives_top_speed_and_does_without_it(fake):
    fake.say("disk-info.txt", BLANK)
    assert CdrdaoBurner().media_info(None).max_write_speed is None  # (it said nothing)
    fake.say("drive-info.txt", DRIVE_INFO)
    info = CdrdaoBurner().media_info(None)
    assert info.max_write_speed == 24 and info.blank is True


def test_a_windows_drive_letter_is_a_device_too():
    assert parse_scanbus("D:\\ : slimtype, eSAU108   3, HL09\n") == [("D:\\", "slimtype, eSAU108   3, HL09")]  # fmt: skip
    assert parse_scanbus("E: : Some, Drive , 1.0") == [("E:", "Some, Drive , 1.0")]
    assert parse_scanbus("/dev/sr0 : TEST, DRIVE , 1.0") == [("/dev/sr0", "TEST, DRIVE , 1.0")]  # fmt: skip
    assert parse_scanbus("ERROR: Cannot setup device : nothing\nCdrdao version 1.2.6 : x\n") == []  # fmt: skip


def test_a_windows_path_is_given_to_cdrdao_in_the_posix_layers_form():
    assert cygwin_path("C:\\Users\\me\\AppData\\Local\\Temp\\dubcrate-x\\disc.iso") == "/cygdrive/c/Users/me/AppData/Local/Temp/dubcrate-x/disc.iso"  # fmt: skip
    assert cygwin_path("D:\\a b\\c.wav") == "/cygdrive/d/a b/c.wav"
    assert cygwin_path("/already/posix.wav") == "/already/posix.wav"


def test_the_toc_names_files_in_the_form_this_cdrdao_wants(tmp_path, monkeypatch):
    from dubcrate.burn import cdrdao as module

    iso = tmp_path / "disc.iso"
    iso.write_bytes(b"")
    assert str(iso.resolve()) in module.build_data_toc(iso)  # (here: the path as it is)
    monkeypatch.setattr(module, "cygwin_path", lambda p: "/cygdrive/x/" + Path(p).name)
    monkeypatch.setattr(module.sys, "platform", "win32")
    assert 'DATAFILE "/cygdrive/x/disc.iso"' in module.build_data_toc(iso)


def test_windows_a_standard_account_that_cannot_open_the_drive_is_told_so(monkeypatch):
    from dubcrate.burn import cdrdao as module

    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(module, "find_cdrdao", lambda: "cdrdao.exe")
    text = "ERROR: Please use option '--device device', e.g. --device /dev/cdrom\nERROR: Cannot setup device E:\\.\n"

    def listed(*pairs):
        monkeypatch.setattr(module, "scan_devices", lambda: list(pairs))

    listed(("E:\\", ", ,"))  # (listed with no description: this account can't open it)
    said = explain_failure(text, "E:\\", 1)
    assert "administrator" in said and "Run as administrator" in said
    listed()  # (not listed at all: unplugged, or the letter moved)
    said = explain_failure(text, "E:\\", 1)
    assert "no drive at E:" in said and "administrator" not in said
    listed(
        ("E:\\", "slimtype, eSAU108   3, HL09")
    )  # (opens fine: the account is not the cause)
    assert "administrator" not in explain_failure(text, "E:\\", 1)
    monkeypatch.setattr(
        module, "find_cdrdao", lambda: None
    )  # (can't be asked: no guess)
    assert "administrator" not in explain_failure(text, "E:\\", 1)
    monkeypatch.setattr("sys.platform", "linux")
    assert "administrator" not in explain_failure(
        text, "/dev/sr0", 1
    )  # (only Windows has this problem)
