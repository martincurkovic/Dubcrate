"""The Disc ID window's rules (Qt-free): which image may be recorded for which MP3 playlist.

The CDJ puts the disc image's creation date (and its label and a few numbers) in the ID it knows an MP3 disc by
(`cdj_card_mp3.Mp3DiscId.made_from`), so Dubcrate can make the card's entry for a disc before the player has seen it,
if it kept what the image was made from. A burn through Dubcrate does that itself (`Library.set_disc_image`); this is
the way for a disc burned before that, or elsewhere: read the disc in the drive (or its image file) and keep that.

An image is accepted for a playlist only when it is plausibly this playlist's disc and the ID can be reproduced here:
the same number of MP3 files and folders, and a label that is the one Dubcrate writes for the playlist, in the bytes it
writes it in (the ID sums them), so the image Sync makes has the same ID. Whether the files themselves are the burned
ones is checked when Sync makes its image (`card_mp3._made_id`: the sector numbers must agree too).
"""

from dubcrate import iso, mp3
from dubcrate.library import Library
from dubcrate.library.models import DiscImage


class ImageError(RuntimeError):
    """An expected problem, in plain words: it is shown to the user as it is."""


def expected_label(lib: Library, disc_id: int) -> str:
    """The Joliet label Dubcrate writes for this playlist."""
    return iso.volume_ident(mp3.volume_name(lib.disc(disc_id).name))


def candidate_problems(
    lib: Library, disc_id: int, found: iso.ImageSummary
) -> list[str]:
    """Why `found` (read from a disc) can't be the image of this playlist's disc; empty when it can."""
    disc = lib.disc(disc_id)
    if disc.kind != "mp3":
        return ["That is an audio playlist: it is known to the card by its TOC."]
    problems = []
    tracks = len(lib.disc_entries(disc_id))
    folders = len(lib.disc_dirs(disc_id))
    if found.file_count != tracks:
        problems.append(
            f"The disc has {found.file_count} MP3 file{'s' if found.file_count != 1 else ''} and the playlist "
            f"{tracks} track{'s' if tracks != 1 else ''}."
        )
    if found.folders != folders:
        problems.append(
            f"The disc has {found.folders} folder{'s' if found.folders != 1 else ''} and the playlist {folders}."
        )
    want = expected_label(lib, disc_id)
    if not found.label_as_ours:
        problems.append(
            "The disc's label is stored in a way Dubcrate doesn't write it, so the player's ID for it can't be made "
            "here: play the disc in the player once instead."
        )
    elif found.label != want:
        problems.append(
            f"The disc's label is “{found.label}” and Dubcrate writes “{want}” for this playlist, so the player's ID "
            "for it can't be made here: play the disc in the player once instead."
        )
    return problems


def record(lib: Library, disc_id: int, found: iso.ImageSummary) -> DiscImage:
    """Keep `found` as the image this playlist's disc was burned from. Raises `ImageError` if it can't be."""
    problems = candidate_problems(lib, disc_id, found)
    if problems:
        raise ImageError(" ".join(problems))
    image = DiscImage(
        found.created.isoformat(timespec="seconds"),
        found.label,
        found.file_count,
        found.folders,
        found.last_file_start,
        found.sectors,
    )
    lib.set_disc_image(disc_id, image)
    return image
