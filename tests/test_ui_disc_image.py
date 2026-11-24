"""The Disc ID window (what the CDJ's ID for an MP3 disc is made from): the record, reading a disc or an image file
(both stood in for), and the rules for accepting one (`image_manager`)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dubcrate import image_manager, iso
from dubcrate.library import Library, TrackRecord
from dubcrate.library.models import DiscImage
from dubcrate.ui.disc_image_dialog import DiscImageDialog

WHEN = datetime(2026, 11, 23, 19, 47, 51, tzinfo=timezone(timedelta(hours=11)))


@pytest.fixture
def lib():
    with Library.open(":memory:") as library:
        yield library


def make_disc(lib, tmp_path, name="MP3 Linux Test", tracks=3, kind="mp3"):
    files = []
    for n in range(tracks):
        f = tmp_path / f"{n}.mp3"
        f.write_bytes(b"x")
        files.append(f)
    lib.replace_source(
        "folder", str(tmp_path), "S",
        [TrackRecord(ext_id=str(n), path=str(f), title=f"T{n}", file_size=1) for n, f in enumerate(files)],
    )  # fmt: skip
    disc_id = lib.create_disc(name, kind=kind)
    lib.add_to_disc(disc_id, [t.id for t in lib.tracks(sort="path")])
    return disc_id


def summary(**kw) -> iso.ImageSummary:
    base = {
        "created": WHEN,
        "label": "MP3 LINUX TEST",
        "label_as_ours": True,
        "file_count": 3,
        "folders": 0,
        "last_file_start": 100,
        "sectors": 200,
    }
    return iso.ImageSummary(**{**base, **kw})


def test_a_disc_with_nothing_recorded_says_so_and_cannot_be_removed(
    lib, tmp_path, qapp
):
    dialog = DiscImageDialog(lib, make_disc(lib, tmp_path))
    assert dialog._values["created"].text() == "Not recorded"
    assert "played it once" in dialog.status.text()
    assert not dialog.remove_button.isEnabled() and dialog.read_button.isEnabled()


def test_what_is_recorded_is_shown_and_can_be_removed(lib, tmp_path, qapp):
    disc_id = make_disc(lib, tmp_path)
    lib.set_disc_image(disc_id, DiscImage(WHEN.isoformat(), "MP3 LINUX TEST", 15, 3, 80245, 84029))  # fmt: skip
    dialog = DiscImageDialog(lib, disc_id)
    assert "2026-11-23 19:47:51" in dialog._values["created"].text()
    assert dialog._values["files"].text() == "15" and "84,029" in dialog._values["size"].text()  # fmt: skip
    changes = []
    dialog.changed.connect(lambda: changes.append(1))
    dialog.remove_button.click()
    assert lib.disc_image(disc_id) is None and changes == [1]


def test_reading_the_disc_in_the_drive_records_it_when_it_is_this_playlists(lib, tmp_path, wait_for):  # fmt: skip
    disc_id = make_disc(lib, tmp_path)
    dialog = DiscImageDialog(lib, disc_id, read_drive=lambda: summary())
    changes = []
    dialog.changed.connect(lambda: changes.append(1))
    dialog.read_button.click()
    assert wait_for(lambda: lib.disc_image(disc_id) is not None)
    image = lib.disc_image(disc_id)
    assert (image.created, image.label, image.file_count, image.folders) == ("2026-11-23T19:47:51+11:00", "MP3 LINUX TEST", 3, 0)  # fmt: skip
    assert image.last_file_start == 100 and image.sectors == 200
    assert "Recorded" in dialog.status.text() and changes == [1]
    assert dialog.read_button.isEnabled()


def test_a_disc_that_is_not_this_playlists_is_refused_in_words_and_nothing_is_kept(lib, tmp_path, wait_for):  # fmt: skip
    disc_id = make_disc(lib, tmp_path)
    for wrong, words in (
        ({"file_count": 4}, "4 MP3 files and the playlist 3 tracks"),
        ({"folders": 2}, "2 folders and the playlist 0"),
        ({"label": "OTHER NAME"}, "writes “MP3 LINUX TEST”"),
        ({"label_as_ours": False}, "stored in a way Dubcrate doesn't write"),
    ):
        dialog = DiscImageDialog(lib, disc_id, read_drive=lambda w=wrong: summary(**w))
        dialog.read_button.click()
        assert wait_for(lambda d=dialog: "Reading" not in d.status.text())
        assert words in dialog.status.text(), dialog.status.text()
        assert lib.disc_image(disc_id) is None


def test_a_failure_to_read_is_said_as_it_is(lib, tmp_path, wait_for):
    from dubcrate.ui.tasks import TaskError

    def no_disc():
        raise TaskError("No optical drive was found.")

    dialog = DiscImageDialog(lib, make_disc(lib, tmp_path), read_drive=no_disc)
    dialog.read_button.click()
    assert wait_for(lambda: "No optical drive" in dialog.status.text())
    assert dialog.read_button.isEnabled()


def test_an_image_file_is_read_through_the_chooser_and_a_cancelled_chooser_does_nothing(lib, tmp_path, wait_for):  # fmt: skip
    folder = tmp_path / "MP3 Linux Test"
    folder.mkdir()
    for n in range(3):
        (folder / f"{n}.mp3").write_bytes(b"x" * 3000)
    image = iso.build_iso(folder, "MP3 Linux Test", tmp_path / "disc.iso", WHEN)
    (tmp_path / "lib").mkdir()
    disc_id = make_disc(lib, tmp_path / "lib")
    dialog = DiscImageDialog(lib, disc_id, choose_file=lambda _p: None)
    dialog.file_button.click()
    assert lib.disc_image(disc_id) is None  # (nothing chosen)
    dialog = DiscImageDialog(lib, disc_id, choose_file=lambda _p: image)
    dialog.file_button.click()
    assert wait_for(lambda: lib.disc_image(disc_id) is not None)
    got = lib.disc_image(disc_id)
    assert got.created == "2026-11-23T19:47:51+11:00" and got.file_count == 3
    assert got.last_file_start == iso.image_layout(image, folder).last_file_start


def test_an_audio_playlist_is_refused(lib, tmp_path):
    disc_id = make_disc(lib, tmp_path, kind="audio")
    assert "audio playlist" in " ".join(image_manager.candidate_problems(lib, disc_id, summary()))  # fmt: skip
    with pytest.raises(image_manager.ImageError):
        image_manager.record(lib, disc_id, summary())


def test_read_image_reads_the_descriptors_and_one_level_of_folders(tmp_path):
    folder = tmp_path / "V"
    (folder / "A").mkdir(parents=True)
    (folder / "A" / "x.mp3").write_bytes(b"x" * 5000)
    (folder / "y.mp3").write_bytes(b"y" * 3000)
    image = iso.build_iso(folder, "Mixed Case Name", tmp_path / "d.iso", WHEN)
    found = iso.read_image(image)
    layout = iso.image_layout(image, folder)
    assert found.created == WHEN and found.label == "MIXED CASE NAME" and found.label_as_ours  # fmt: skip
    assert (found.file_count, found.folders) == (2, 1)
    assert found.last_file_start == layout.last_file_start and found.sectors == layout.sectors  # fmt: skip
    assert layout.label == found.label
    (folder / "A" / "B").mkdir()
    nested = iso.build_iso(folder, "Mixed Case Name", tmp_path / "n.iso", WHEN)
    with pytest.raises(iso.IsoError, match="inside folders"):
        iso.read_image(nested)
    with pytest.raises(iso.IsoError):
        iso.read_image(tmp_path / "not-there.iso")
    bare = Path(tmp_path / "bare.iso")
    bare.write_bytes(bytes(40 * 2048))
    with pytest.raises(iso.IsoError, match="Joliet"):
        iso.read_image(bare)


def test_a_labels_field_is_ours_only_when_padded_as_pycdlib_pads_it():
    assert iso.joliet_label_field("AB") == b"\x00A\x00B" + b"\x00\x20" * 14
    assert len(iso.joliet_label_field("X" * 16)) == 32


def test_the_data_disc_device_is_a_drive_on_linux_and_an_error_elsewhere(monkeypatch):
    from dubcrate import drive_toc

    monkeypatch.setattr("sys.platform", "linux")
    assert drive_toc.data_disc_device(lambda p: p == "/dev/sr0") == Path(
        "/dev/sr0"
    )  # (not str(): Windows spells it \\dev\\sr0)
    with pytest.raises(drive_toc.DriveTocError, match="No optical drive"):
        drive_toc.data_disc_device(lambda p: False)
    monkeypatch.setattr("sys.platform", "darwin")
    with pytest.raises(drive_toc.DriveTocError, match="image file"):
        drive_toc.data_disc_device()
