"""The TOC of the disc in the drive (`drutil` is stood in for: no drive here)."""

import subprocess
import sys
from pathlib import Path

import pytest

from dubcrate import cdj_card as cc
from dubcrate import drive_toc
from dubcrate.drive_toc import DriveTocError, parse_drive_toc, read_drive_toc

DATA = Path(__file__).parent / "data" / "cdj_card"


def saved(name="CD2_TOC.txt"):
    return (DATA / name).read_text(encoding="utf-8")


def finished(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["drutil", "toc"], code, stdout, stderr)


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


def test_an_audio_disc_gives_its_track_starts_and_lead_out():
    assert parse_drive_toc(saved()) == cc.toc_from_drutil(saved())


def test_it_runs_drutil_toc_without_a_terminal_and_reads_what_it_prints(on_a_mac):
    seen = {}

    def run(command, **kw):
        seen.update(command=command, **kw)
        return finished(saved())

    assert read_drive_toc(run) == cc.toc_from_drutil(saved())
    assert seen["command"] == ["drutil", "toc"]
    assert seen["check"] is False and seen["stdin"] is subprocess.DEVNULL
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert seen["timeout"] == drive_toc.TIMEOUT_SECONDS


def test_no_disc_says_what_drutil_said(on_a_mac):
    def run(command, **kw):
        return finished("", "drutil: Please insert a disc.", code=1)

    with pytest.raises(DriveTocError, match="no readable disc.*Please insert a disc"):
        read_drive_toc(run, sleep=lambda _s: None)


def test_no_output_at_all_is_still_a_plain_message(on_a_mac):
    with pytest.raises(DriveTocError, match="drutil printed nothing"):
        read_drive_toc(lambda *a, **kw: finished(), sleep=lambda _s: None)


def test_a_disc_that_is_not_plain_audio_is_refused(on_a_mac):
    data_disc = saved().replace("2ch audio", "data", 1)
    with pytest.raises(DriveTocError, match="isn't a plain audio CD"):
        read_drive_toc(lambda *a, **kw: finished(data_disc))


def test_a_drive_that_does_not_answer_or_a_missing_drutil_is_an_expected_error(
    on_a_mac,
):
    def hangs(*a, **kw):
        raise subprocess.TimeoutExpired("drutil", 60)

    def missing(*a, **kw):
        raise FileNotFoundError("drutil")

    with pytest.raises(DriveTocError, match="didn't answer"):
        read_drive_toc(hangs)
    with pytest.raises(DriveTocError, match="Couldn't run drutil"):
        read_drive_toc(missing)


def test_a_system_with_no_way_yet_says_so_and_runs_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd14")

    def run(*a, **kw):
        raise AssertionError("must not run")

    with pytest.raises(DriveTocError, match="isn't done on this kind of computer"):
        read_drive_toc(run)
    assert not drive_toc.can_read_toc()


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_the_three_systems_can_read_a_toc(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    assert drive_toc.can_read_toc()


def test_a_drive_that_prints_only_its_own_name_is_reported_as_having_no_disc(on_a_mac):
    header = " Vendor   Product           Rev \n slimtype eSAU108   3       HL09\n"  # (seen, with no disc)
    with pytest.raises(
        DriveTocError, match=r"no readable disc.*the drive reported no disc"
    ):
        read_drive_toc(lambda *a, **kw: finished(header), sleep=lambda _s: None)


def test_a_read_that_finds_no_disc_is_tried_again_and_a_later_answer_is_used(on_a_mac):
    answers = [
        finished("", "No media, please insert a CD to read its TOC/ATIP."),
        finished(saved()),
    ]
    slept = []
    result = read_drive_toc(lambda *a, **kw: answers.pop(0), sleep=slept.append)
    assert result == cc.toc_from_drutil(saved()) and slept == [drive_toc.RETRY_SECONDS]


def test_a_drive_that_keeps_saying_no_media_gives_up_after_a_few_tries(on_a_mac):
    calls = []

    def empty(*a, **kw):
        calls.append(1)
        return finished("", "No media, please insert a CD to read its TOC/ATIP.")

    with pytest.raises(DriveTocError, match="No media, please insert a CD"):
        read_drive_toc(empty, sleep=lambda _s: None)
    assert len(calls) == drive_toc.ATTEMPTS


def test_a_disc_that_is_not_audio_is_refused_at_once_not_tried_again(on_a_mac):
    calls = []
    data_disc = saved().replace("2ch audio", "data", 1)

    def data(*a, **kw):
        calls.append(1)
        return finished(data_disc)

    with pytest.raises(DriveTocError, match="isn't a plain audio CD"):
        read_drive_toc(data, sleep=lambda _s: None)
    assert calls == [1]


# -- Linux and Windows: the entries a driver gives -----------------------------------------

E = drive_toc.Entry
LEAD = drive_toc.LEAD_OUT


def audio_entries():
    """A three-track audio disc: starts 00:02.00, 03:00.00, 06:30.10 and a lead-out at 10:00.00."""
    return [
        E(1, 0, 0, 2, 0),
        E(2, 0, 3, 0, 0),
        E(3, 0, 6, 30, 10),
        E(LEAD, 0, 10, 0, 0),
    ]


def test_entries_become_absolute_frames_in_track_order():
    starts, lead_out = drive_toc.toc_from_entries(list(reversed(audio_entries())))
    assert starts == [cc.frames(0, 2), cc.frames(3, 0), cc.frames(6, 30, 10)]
    assert lead_out == cc.frames(10, 0)


def test_the_entries_agree_with_what_drutil_prints_for_the_same_disc():
    starts, lead_out = cc.toc_from_drutil(saved())
    msf = lambda f: (f // 4500, f // 75 % 60, f % 75)
    entries = [E(n, 0, *msf(f)) for n, f in enumerate(starts, 1)] + [E(LEAD, 0, *msf(lead_out))]  # fmt: skip
    assert drive_toc.toc_from_entries(entries) == (starts, lead_out)


def test_a_disc_with_a_data_track_is_refused_not_tried_again():
    entries = audio_entries()
    entries[2] = entries[2]._replace(control=4)
    with pytest.raises(DriveTocError, match="isn't a plain audio CD") as caught:
        drive_toc.toc_from_entries(entries)
    assert not isinstance(caught.value, drive_toc._NoDisc)


@pytest.mark.parametrize("entries", [[], [E(1, 0, 0, 2, 0)], [E(LEAD, 0, 10, 0, 0)]])
def test_entries_with_no_tracks_or_no_lead_out_are_no_disc(entries):
    with pytest.raises(drive_toc._NoDisc):
        drive_toc.toc_from_entries(entries)


class FakeDrive:
    """The kernel's CD-ROM ioctls: answers CDROMREADTOCHDR and CDROMREADTOCENTRY for a disc."""

    def __init__(self, entries, fail=None):
        self.entries = {e.number: e for e in entries}
        self.fail, self.calls = fail, []

    def open(self, path, flags):
        self.calls.append(("open", path, flags))
        return 7

    def ioctl(self, fd, request, buffer):
        self.calls.append(("ioctl", request))
        if self.fail:
            raise self.fail
        if request == drive_toc.CDROMREADTOCHDR:
            tracks = [n for n in self.entries if n != LEAD]
            buffer[0:2] = bytes([min(tracks), max(tracks)])
        else:
            import struct

            number = buffer[0]
            e = self.entries[number]
            assert (
                buffer[2] == drive_toc.CDROM_MSF
            )  # (asked for minutes, seconds, frames)
            buffer[:] = struct.pack(drive_toc._TOC_ENTRY, number, e.control << 4 | 1, 2, bytes([e.minute, e.second, e.frame]), 0)  # fmt: skip


def test_linux_reads_the_header_then_each_track_and_the_lead_out(monkeypatch):
    monkeypatch.setattr("os.close", lambda fd: None)
    drive = FakeDrive(audio_entries())
    found = drive_toc.linux_entries("/dev/sr0", os_open=drive.open, ioctl=drive.ioctl)
    assert found == audio_entries()
    opened = drive.calls[0]
    assert opened[:2] == ("open", "/dev/sr0") and opened[2] & getattr(
        __import__("os"), "O_NONBLOCK", 0
    ) == getattr(__import__("os"), "O_NONBLOCK", 0)
    assert [c[1] for c in drive.calls[1:]] == [drive_toc.CDROMREADTOCHDR] + [drive_toc.CDROMREADTOCENTRY] * 4  # fmt: skip


def test_linux_the_default_drive_is_the_first_that_exists(monkeypatch):
    monkeypatch.setattr("os.close", lambda fd: None)
    drive = FakeDrive(audio_entries())
    drive_toc.linux_entries(os_open=drive.open, ioctl=drive.ioctl, exists=lambda p: p == "/dev/sr0")  # fmt: skip
    assert drive.calls[0][1] == "/dev/sr0"
    with pytest.raises(DriveTocError, match="No disc drive was found"):
        drive_toc.linux_entries(os_open=drive.open, ioctl=drive.ioctl, exists=lambda p: False)  # fmt: skip


def test_linux_an_empty_drive_is_no_disc_and_a_locked_one_says_the_group(monkeypatch):
    import errno

    monkeypatch.setattr("os.close", lambda fd: None)
    empty = FakeDrive([], fail=OSError(drive_toc.ENOMEDIUM, "No medium found"))
    with pytest.raises(drive_toc._NoDisc, match="no readable disc"):
        drive_toc.linux_entries("/dev/sr0", os_open=empty.open, ioctl=empty.ioctl)

    def locked(path, flags):
        raise PermissionError(errno.EACCES, "Permission denied")

    with pytest.raises(DriveTocError, match="`cdrom` or `optical`") as caught:
        drive_toc.linux_entries("/dev/sr0", os_open=locked, ioctl=lambda *a: None)
    assert not isinstance(caught.value, drive_toc._NoDisc)


def test_linux_read_drive_toc_uses_the_kernel_and_retries_an_empty_drive(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    answers = [drive_toc._NoDisc("no disc"), audio_entries()]

    def entries():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(drive_toc, "linux_entries", entries)
    slept = []
    result = read_drive_toc(lambda *a, **kw: pytest.fail("ran a command"), sleep=slept.append)  # fmt: skip
    assert result[1] == cc.frames(10, 0) and slept == [drive_toc.RETRY_SECONDS]


def windows_buffer(entries, trailing=b""):
    """A CDROM_TOC as the driver fills it: big-endian length, first, last, 8 bytes an entry."""
    import struct

    body = b"".join(
        struct.pack(
            "8B", 0, e.control << 4 | 1, e.number, 0, 0, e.minute, e.second, e.frame
        )
        for e in entries
    )
    tracks = [e.number for e in entries if e.number != LEAD]
    return (
        struct.pack(">HBB", len(body) + 2, min(tracks), max(tracks)) + body + trailing
    )


def test_windows_buffer_is_read_into_entries():
    assert (
        drive_toc.parse_windows_toc(windows_buffer(audio_entries())) == audio_entries()
    )


def test_windows_ignores_what_follows_the_entries_the_length_names():
    data = windows_buffer(audio_entries(), trailing=bytes(8) * 3)
    assert drive_toc.parse_windows_toc(data) == audio_entries()


def test_windows_a_short_or_empty_buffer_is_no_disc():
    for data in (b"", b"\x00\x02"):
        with pytest.raises(drive_toc._NoDisc):
            drive_toc.parse_windows_toc(data)


def test_windows_read_drive_toc_goes_through_the_entries(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(drive_toc, "windows_entries", audio_entries)
    starts, lead_out = read_drive_toc(lambda *a, **kw: pytest.fail("ran a command"))
    assert starts[0] == cc.frames(0, 2) and lead_out == cc.frames(10, 0)


# -- is a disc in the drive? (cheap, never spins the drive) ---------------------------------------


@pytest.mark.parametrize(
    "status, expected",
    [(1, "empty"), (2, "empty"), (3, "loading"), (4, "disc"), (0, None), (99, None)],
)
def test_linux_asks_the_kernel_whether_a_disc_is_in(monkeypatch, status, expected):
    monkeypatch.setattr(sys, "platform", "linux")
    asked = []

    def ioctl(fd, request, arg):
        asked.append((request, arg))
        return status

    closed = []
    monkeypatch.setattr("os.close", closed.append)
    got = drive_toc.disc_state(
        "/dev/sr0", os_open=lambda path, flags: 7, ioctl=ioctl, exists=lambda p: True
    )
    assert got == expected and closed == [7]
    assert asked == [(drive_toc.CDROM_DRIVE_STATUS, drive_toc.CDSL_CURRENT)]


def test_linux_without_a_drive_or_without_permission_cannot_say(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert drive_toc.disc_state(exists=lambda p: False) is None  # no drive found

    def refuse(path, flags):
        raise PermissionError(13, "denied")

    assert (
        drive_toc.disc_state("/dev/sr0", os_open=refuse) is None
    )  # the full check says why


def test_linux_opens_the_drive_without_blocking_and_ignores_a_drive_that_is_not_a_device_path(
    monkeypatch,
):
    monkeypatch.setattr(sys, "platform", "linux")
    opened = []

    def open_(path, flags):
        opened.append((path, flags))
        return 5

    monkeypatch.setattr("os.close", lambda fd: None)
    drive_toc.disc_state(
        "disk3", os_open=open_, ioctl=lambda *a: 4, exists=lambda p: p == "/dev/sr0"
    )  # (a macOS-style name: fall back to the first drive found)
    ((path, flags),) = opened
    assert path == "/dev/sr0" and flags & __import__("os").O_NONBLOCK


@pytest.mark.parametrize(
    "text, expected",
    [
        (" Vendor   Product           Rev \n LITE-ON  eSAU108 3  HL09\n\n           Type: No Media Inserted\n", "empty"),
        (" Vendor   Product           Rev \n LITE-ON  eSAU108 3  HL09\n\n           Type: CD-R        Name: /dev/disk4\n", "disc"),
        ("something else", None),
    ],
)  # fmt: skip
def test_a_mac_reads_drutil_status(monkeypatch, text, expected):
    monkeypatch.setattr(sys, "platform", "darwin")
    got = drive_toc.disc_state(run=lambda *a, **k: finished(text))
    assert got == expected


def test_windows_cannot_say(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert drive_toc.disc_state() is None
