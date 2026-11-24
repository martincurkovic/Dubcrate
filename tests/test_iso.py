"""The ISO image of an MP3 disc (made in Python with pycdlib; read back here, and by xorriso when it is there)."""

import shutil
import subprocess

import pycdlib
import pytest

from dubcrate import iso
from dubcrate.burn import BurnError, BurnOptions
from dubcrate.burn.base import DataDisc
from dubcrate.burn.cdrdao import CdrdaoBurner, build_data_toc
from dubcrate.cd import cdtext_toc_escape
from dubcrate.iso import IsoError, build_iso, volume_ident

posix = pytest.mark.skipif(
    __import__("sys").platform == "win32", reason="the stand-ins are shell scripts"
)


def disc_folder(tmp_path):
    """MIXED BAG: two files and two folders (one inside the other), with accents in a name."""
    folder = tmp_path / "MIXED BAG"
    (folder / "Warm Ups" / "Deep").mkdir(parents=True)
    (folder / "02 B.mp3").write_bytes(b"bbbb")
    (folder / "01 A \u00e9.mp3").write_bytes(b"aa")
    (folder / "Warm Ups" / "01 C.mp3").write_bytes(b"c" * 5000)
    (folder / "Warm Ups" / "Deep" / "01 D.mp3").write_bytes(b"dddddd")
    (folder / "Another").mkdir()
    return folder


def opened(path):
    image = pycdlib.PyCdlib()
    image.open(str(path))
    return image


def names(image, **where):
    out = []
    for child in image.list_children(**where):
        if child.is_dot() or child.is_dotdot():
            continue
        raw = child.file_identifier()
        out.append(
            raw.decode("utf-16-be") if "joliet_path" in where else raw.decode("ascii")
        )
    return out


def content(image, **where):
    import io

    buffer = io.BytesIO()
    image.get_file_from_iso_fp(buffer, **where)
    return buffer.getvalue()


def test_the_image_has_joliet_names_in_order_and_the_files_inside(tmp_path):
    out = build_iso(disc_folder(tmp_path), "MIXED BAG", tmp_path / "disc.iso")
    assert (
        out.stat().st_size > 2048 * 16 and out.read_bytes()[32769:32774] == b"CD001"
    )  # (sector 16)
    image = opened(out)
    try:
        assert image.has_joliet() and image.has_rock_ridge()
        assert names(image, joliet_path="/") == [
            "01 A \u00e9.mp3",
            "02 B.mp3",
            "Another",
            "Warm Ups",
        ]
        assert names(image, joliet_path="/Warm Ups") == ["01 C.mp3", "Deep"]
        assert content(image, joliet_path="/01 A \u00e9.mp3") == b"aa"
        assert content(image, joliet_path="/Warm Ups/Deep/01 D.mp3") == b"dddddd"
        assert len(content(image, joliet_path="/Warm Ups/01 C.mp3")) == 5000
    finally:
        image.close()


def test_the_plain_iso_names_are_numbered_in_the_order_of_the_real_ones(tmp_path):
    image = opened(build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso"))
    try:
        assert names(image, iso_path="/") == ["001.MP3;1", "002.MP3;1", "D001", "D002"]
        assert names(image, iso_path="/D002") == [
            "001.MP3;1",
            "D001",
        ]  # (Warm Ups: Another is D001)
        assert content(image, iso_path="/001.MP3;1") == b"aa"
    finally:
        image.close()


def test_rock_ridge_carries_the_real_names_too(tmp_path):
    image = opened(build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso"))
    try:
        assert content(image, rr_path="/Warm Ups/Deep/01 D.mp3") == b"dddddd"
        assert image.get_record(rr_path="/01 A \u00e9.mp3").is_file()
    finally:
        image.close()


def test_the_volume_label_is_ascii_capitals_and_sixteen_characters(tmp_path):
    assert volume_ident("MIXED BAG") == "MIXED BAG"
    assert volume_ident("Caf\u00e9 Sets: Vol. 2 of many") == "CAFE SETS VOL 2"
    assert volume_ident("\u30c7\u30a3\u30b9\u30af") == "MP3 DISC"  # nothing ASCII left
    assert volume_ident("") == "MP3 DISC"
    image = opened(
        build_iso(disc_folder(tmp_path), "Caf\u00e9 Sets", tmp_path / "disc.iso")
    )
    try:
        assert image.pvd.volume_identifier.decode("ascii").strip() == "CAFE SETS"
        assert (
            image.pvd.application_identifier.text.decode("ascii").strip()
            == iso.APPLICATION
        )
    finally:
        image.close()


def test_a_folder_of_many_files_gets_enough_digits_for_its_plain_names(tmp_path):
    folder = tmp_path / "BIG"
    folder.mkdir()
    for n in range(1, 1001):  # (more than the 999 a disc allows: the digits just grow)
        (folder / f"{n:04d}.mp3").write_bytes(b"x")
    image = opened(build_iso(folder, "V", tmp_path / "disc.iso"))
    try:
        listed = names(image, iso_path="/")
        assert (
            listed[0] == "0001.MP3;1"
            and listed[-1] == "1000.MP3;1"
            and len(listed) == 1000
        )
    finally:
        image.close()


def test_eight_folders_deep_is_made(tmp_path):
    folder = tmp_path / "DEEP"
    here = folder
    for n in range(1, 9):
        here = here / f"Level {n}"
    here.mkdir(parents=True)
    (here / "01 End.mp3").write_bytes(b"end")
    image = opened(build_iso(folder, "V", tmp_path / "disc.iso"))
    try:
        path = "/".join(f"Level {n}" for n in range(1, 9))
        assert content(image, joliet_path=f"/{path}/01 End.mp3") == b"end"
    finally:
        image.close()


def test_an_empty_folder_is_a_valid_image(tmp_path):
    (tmp_path / "EMPTY").mkdir()
    out = build_iso(tmp_path / "EMPTY", "V", tmp_path / "disc.iso")
    image = opened(out)
    try:
        assert names(image, joliet_path="/") == []
    finally:
        image.close()


def test_the_folder_is_only_read_and_a_second_build_replaces_the_first(tmp_path):
    folder = disc_folder(tmp_path)
    before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
    out = build_iso(folder, "V", tmp_path / "disc.iso")
    first = out.stat().st_size
    (folder / "03 New.mp3").write_bytes(b"n" * 9000)
    assert build_iso(folder, "V", tmp_path / "disc.iso").stat().st_size > first
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == sorted([*before, "03 New.mp3"])  # fmt: skip
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] == []


def test_a_failure_is_an_expected_error_and_leaves_no_half_made_file(
    tmp_path, monkeypatch
):
    def fails(self, path, *a, **k):
        with open(path, "wb") as half:
            half.write(b"half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pycdlib.PyCdlib, "write", fails)
    with pytest.raises(IsoError, match="could not be made: .*No space left"):
        build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso")
    assert not (tmp_path / "disc.iso").exists() and not list(tmp_path.glob("*.part"))


def test_a_name_the_format_refuses_is_an_expected_error(tmp_path):
    folder = tmp_path / "ODD"
    folder.mkdir()
    (folder / ("x" * 70 + ".mp3")).write_bytes(
        b"x"
    )  # (longer than Joliet and Rock Ridge allow)
    with pytest.raises(IsoError, match="could not be made"):
        build_iso(folder, "V", tmp_path / "disc.iso")
    assert not (tmp_path / "disc.iso").exists()


def test_a_folder_that_is_not_there_is_an_expected_error(tmp_path):
    with pytest.raises(IsoError, match="is not a folder"):
        build_iso(tmp_path / "gone", "V", tmp_path / "disc.iso")


@pytest.mark.skipif(shutil.which("xorriso") is None, reason="no xorriso here")
def test_xorriso_lists_the_same_disc(tmp_path):
    out = build_iso(disc_folder(tmp_path), "MIXED BAG", tmp_path / "disc.iso")
    # (Rock Ridge names, which xorriso reads by default, and the volume id)
    proc = subprocess.run(
        ["xorriso", "-indev", str(out), "-find"],
        check=False, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )  # fmt: skip
    listing = proc.stdout + proc.stderr  # (the volume id is a message, on stderr)
    assert "Volume id    : 'MIXED BAG'" in listing
    for name in ("./01 A \u00e9.mp3", "./Warm Ups/Deep/01 D.mp3", "./Another"):
        assert f"'{name}'" in listing


def test_the_data_disc_toc_is_one_mode1_track_of_the_image(tmp_path):
    image = tmp_path / "disc.iso"
    assert (
        build_data_toc(image)
        == f'CD_ROM\n\nTRACK MODE1\nDATAFILE "{cdtext_toc_escape(str(image.resolve()))}"\n'
    )
    assert 'DATAFILE "disc.iso"' in build_data_toc(image, relative_to=tmp_path)


# -- burning an MP3 disc with cdrdao -----------------------------------------------------


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    from dubcrate import tools

    folder = tmp_path / "bundle"
    folder.mkdir()
    monkeypatch.setattr(tools, "BUNDLE_DIR", folder)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    return folder


def stand_in(folder, name, body):
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@posix
def test_an_mp3_disc_is_burned_as_an_image_through_cdrdao(
    bundle, tmp_path, monkeypatch
):
    log = tmp_path / "cdrdao.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    stand_in(
        bundle,
        "cdrdao",
        'echo "$@" >> "$FAKE_LOG"\n'
        'for last; do :; done\ncat "$last" >> "$FAKE_LOG"\n'  # the TOC it was given
        'echo "Writing finished successfully."\n',
    )
    said = []
    CdrdaoBurner().burn_data(
        DataDisc("MIXED BAG", disc_folder(tmp_path)),
        BurnOptions(speed=10, device="/dev/sr0", eject=False),
        said.append,
    )
    text = log.read_text()
    assert "write --device /dev/sr0 -n --speed 10 " in text
    assert "CD_ROM" in text and "TRACK MODE1" in text and 'DATAFILE "' in text and "disc.iso" in text  # fmt: skip
    assert said[0].startswith("making the disc image") and said[-1] == "Writing finished successfully."  # fmt: skip


@posix
def test_an_image_that_cannot_be_made_is_said_before_touching_the_drive(bundle, tmp_path, monkeypatch):  # fmt: skip
    log = tmp_path / "cdrdao.log"
    stand_in(bundle, "cdrdao", f'echo "$@" >> {log}\n')

    def fails(*a, **k):
        raise IsoError("The disc image could not be made: out of room")

    monkeypatch.setattr("dubcrate.burn.cdrdao.build_iso", fails)
    with pytest.raises(BurnError, match="out of room"):
        CdrdaoBurner().burn_data(
            DataDisc("V", disc_folder(tmp_path)), BurnOptions(device="/dev/sr0"), print
        )
    assert not log.exists()


def test_the_creation_date_can_be_pinned_and_it_makes_the_same_cdj_id(tmp_path):
    from datetime import datetime, timedelta, timezone

    from dubcrate import cdj_card_mp3 as m3

    folder = tmp_path / "V"
    (folder / "A").mkdir(parents=True)
    (folder / "A" / "x.mp3").write_bytes(b"x" * 5000)
    (folder / "y.mp3").write_bytes(b"y" * 3000)
    when = datetime(2026, 11, 23, 19, 47, 51, tzinfo=timezone(timedelta(hours=11)))
    ids = []
    for name in ("one", "two"):  # two images made at different moments, one date
        image = build_iso(folder, "MP3 Linux Test", tmp_path / f"{name}.iso", when)
        svd = iso.joliet_descriptor(image)
        assert svd[0] == 2 and svd[813:830] == b"2026112319475100,"
        pvd = image.read_bytes()[16 * 2048 : 17 * 2048]
        assert pvd[0] == 1 and pvd[813:830] == b"2026112319475100,"
        layout = iso.image_layout(image, folder)
        assert (layout.file_count, layout.folders) == (2, 1)
        ids.append(
            m3.Mp3DiscId.made_from(
                svd,
                file_count=layout.file_count,
                folder_count=layout.folders,
                last_file_start=layout.last_file_start,
            )
        )
    assert ids[0] == ids[1] and ids[0].raw[4:8].hex() == "338745e7"
    later = build_iso(
        folder, "MP3 Linux Test", tmp_path / "later.iso", when + timedelta(seconds=1)
    )
    other = m3.Mp3DiscId.made_from(
        iso.joliet_descriptor(later), file_count=2, folder_count=1, last_file_start=0
    )
    assert other.raw[4:8] != ids[0].raw[4:8]


def test_without_a_date_the_image_is_made_now_and_a_missing_descriptor_is_an_error(tmp_path):  # fmt: skip
    folder = tmp_path / "V"
    folder.mkdir()
    (folder / "y.mp3").write_bytes(b"y")
    image = build_iso(folder, "V", tmp_path / "v.iso")
    assert iso.joliet_descriptor(image)[813:817].isdigit()
    bare = tmp_path / "bare.iso"
    bare.write_bytes(bytes(40 * 2048))
    with pytest.raises(IsoError):
        iso.joliet_descriptor(bare)


@posix
def test_a_burn_makes_the_image_with_a_date_and_says_what_it_looked_like(bundle, tmp_path):  # fmt: skip
    from dubcrate.build import image_record

    stand_in(bundle, "cdrdao", 'echo "Writing finished successfully."\n')
    folder = tmp_path / "SIMPLE"
    folder.mkdir()
    (folder / "01 A.mp3").write_bytes(b"a" * 4000)
    disc = DataDisc("SIMPLE", folder)
    CdrdaoBurner().burn_data(disc, BurnOptions(device="/dev/sr0", eject=False), print)
    assert disc.created is not None and disc.layout is not None
    record = image_record(disc)
    assert (record.label, record.file_count, record.folders) == ("SIMPLE", 1, 0)
    assert record.sectors == disc.layout.sectors and record.created.startswith(
        str(disc.created.year)
    )
    # a disc with a date already (staged earlier) keeps it
    fixed = disc.created.replace(year=2020)
    again = DataDisc("SIMPLE", folder, created=fixed)
    CdrdaoBurner().burn_data(again, BurnOptions(device="/dev/sr0", eject=False), print)
    assert again.created == fixed
    # a folder in a folder: written all the same, but not recorded (how the player numbers it is not known)
    nested = DataDisc("MIXED BAG", disc_folder(tmp_path))
    CdrdaoBurner().burn_data(nested, BurnOptions(device="/dev/sr0", eject=False), print)
    assert nested.layout is None and image_record(nested) is None
