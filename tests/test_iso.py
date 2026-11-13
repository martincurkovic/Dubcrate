"""The ISO image of an MP3 disc: which tool, with which options, and the data-disc TOC."""

import sys

import pytest

from dubcrate import iso, tools
from dubcrate.burn import BurnError, BurnOptions
from dubcrate.burn.base import DataDisc
from dubcrate.burn.cdrdao import CdrdaoBurner, build_data_toc
from dubcrate.iso import IsoError, build_iso, find_iso_tool

posix = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-ins are shell scripts"
)


def stand_in(folder, name, body):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


# a tool that records its arguments and writes the image it was asked for (-o FILE)
RECORD_AND_WRITE = """printf '%s\\n' "$@" > "$FAKE_ARGS"
while [ "$#" -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
echo "an image" > "$out"
"""


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    folder = tmp_path / "bundle"
    folder.mkdir()
    monkeypatch.setattr(tools, "BUNDLE_DIR", folder)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args"))
    return folder


def disc_folder(tmp_path):
    folder = tmp_path / "MIXED BAG"
    (folder / "Sub").mkdir(parents=True, exist_ok=True)
    (folder / "01 A.mp3").write_bytes(b"x")
    return folder


@posix
def test_xorriso_is_asked_for_joliet_and_rock_ridge_and_the_volume_name(
    bundle, tmp_path
):
    stand_in(bundle, "xorriso", RECORD_AND_WRITE)
    out = build_iso(disc_folder(tmp_path), "MIXED BAG", tmp_path / "disc.iso")
    assert out.read_text().strip() == "an image"
    args = (tmp_path / "args").read_text().splitlines()
    assert args[:2] == ["-as", "mkisofs"]
    assert "-J" in args and "-r" in args and args[args.index("-V") + 1] == "MIXED BAG"  # fmt: skip
    assert args[-1] == str(tmp_path / "MIXED BAG")


@posix
def test_genisoimage_takes_the_same_options_without_the_xorriso_prefix(
    bundle, tmp_path
):
    stand_in(bundle, "genisoimage", RECORD_AND_WRITE)
    assert find_iso_tool().name == "genisoimage"
    build_iso(disc_folder(tmp_path), "VOL", tmp_path / "disc.iso")
    assert (tmp_path / "args").read_text().splitlines()[0] == "-quiet"


@posix
def test_xorriso_is_preferred_over_the_others(bundle):
    for name in ("mkisofs", "genisoimage", "xorriso"):
        stand_in(bundle, name, "exit 0\n")
    assert find_iso_tool().name == "xorriso"


@posix
def test_a_tool_that_fails_or_writes_nothing_is_an_expected_error(bundle, tmp_path):
    stand_in(
        bundle, "xorriso", 'echo "xorriso : FAILURE : something broke" >&2\nexit 5\n'
    )
    with pytest.raises(
        IsoError, match="xorriso could not make the image: xorriso : FAILURE"
    ):
        build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso")
    stand_in(bundle, "xorriso", "exit 0\n")  # claims success, writes nothing
    with pytest.raises(IsoError, match="it wrote nothing"):
        build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso")
    assert not (tmp_path / "disc.iso").exists()


def test_no_tool_says_how_to_get_one(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(iso.sys, "platform", "linux")
    with pytest.raises(IsoError, match="sudo apt install xorriso"):
        build_iso(disc_folder(tmp_path), "V", tmp_path / "disc.iso")


def test_a_folder_that_is_not_there_is_an_expected_error(bundle, tmp_path):
    stand_in(bundle, "xorriso", "exit 0\n")
    with pytest.raises(IsoError, match="is not a folder"):
        build_iso(tmp_path / "gone", "V", tmp_path / "disc.iso")


@pytest.mark.skipif(find_iso_tool() is None, reason="no ISO tool here")
def test_the_real_tool_makes_an_image(tmp_path):
    folder = disc_folder(tmp_path)
    out = build_iso(folder, "MIXED BAG", tmp_path / "disc.iso")
    assert out.stat().st_size > 2048 * 16  # a volume descriptor starts at sector 16
    assert b"CD001" in out.read_bytes()[32768:32775]  # ISO 9660's mark


def test_the_data_disc_toc_is_one_mode1_track_of_the_image(tmp_path):
    image = tmp_path / "disc.iso"
    assert (
        build_data_toc(image)
        == f'CD_ROM\n\nTRACK MODE1\nDATAFILE "{image.resolve()}"\n'
    )
    assert 'DATAFILE "disc.iso"' in build_data_toc(image, relative_to=tmp_path)


# -- burning an MP3 disc with cdrdao -----------------------------------------------------


@posix
def test_an_mp3_disc_is_burned_as_an_image_through_cdrdao(
    bundle, tmp_path, monkeypatch
):
    stand_in(bundle, "xorriso", RECORD_AND_WRITE)
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
def test_an_mp3_burn_without_an_image_tool_says_so_before_touching_the_drive(
    bundle, tmp_path
):
    log = tmp_path / "cdrdao.log"
    stand_in(bundle, "cdrdao", f'echo "$@" >> {log}\n')
    with pytest.raises(BurnError, match="xorriso"):
        CdrdaoBurner().burn_data(
            DataDisc("V", disc_folder(tmp_path)), BurnOptions(device="/dev/sr0"), print
        )
    assert not log.exists()
