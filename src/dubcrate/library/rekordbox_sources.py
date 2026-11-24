"""What differs between the two kinds of source that carry rekordbox's data, in one place (Qt-free, read-only).

- `rekordbox`: a computer's `master.db` (the source's path is that file). Its analysis files live in the `share`
  folder beside it, and its cues in the database.
- `rekordbox_usb`: a USB stick (the source's path is the stick's root). Its analysis files are under the root, and
  its cues in `PIONEER/rekordbox/exportLibrary.db`.

Whatever READS rekordbox's cues, beat grids or waveforms asks here, so a stick works the same as `master.db`. **Whatever
WRITES to rekordbox (`rekordbox_write`, `ui/rekordbox_editor.py`) must not**: it acts on `kind == "rekordbox"` only, and
a stick is never written. `dev_docs/rekordbox-usb.md`.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from dubcrate.analysis import Cue, share_root
from dubcrate.library import rekordbox_usb
from dubcrate.library.rekordbox import read_cues

KINDS = (
    "rekordbox",
    "rekordbox_usb",
)  # the kinds of source that carry rekordbox's data


def carries_rekordbox_data(kind: str) -> bool:
    return kind in KINDS


def analysis_root(kind: str, path: str | Path) -> Path:
    """The folder rekordbox's analysis paths (`/PIONEER/USBANLZ/...`) are relative to, for a source of this kind."""
    return Path(path) if kind == "rekordbox_usb" else share_root(path)


def cue_database(kind: str, path: str | Path) -> Path:
    """The file a source's cues are read from (and the one whose size and date say they changed): `master.db`, or a
    stick's `exportLibrary.db` (where it would be, if the stick is not plugged in)."""
    if kind != "rekordbox_usb":
        return Path(path)
    root = Path(path)
    return rekordbox_usb.library_file(root) or (
        root / rekordbox_usb.PIONEER_NAMES[0] / "rekordbox" / rekordbox_usb.DB_NAME
    )


def read_cues_at(
    path: str | Path,
    master_reader: Callable[[Path], Mapping[str, Sequence[Cue]]] = read_cues,
) -> Mapping[str, Sequence[Cue]]:
    """Every cue in the database at `path`: a `master.db` (read by `master_reader`, which a caller can name so its
    own module's `read_cues` stays the one place a test replaces), an `exportLibrary.db`, or a stick's root.
    Read-only (a snapshot copy), raises `RekordboxError` in plain words."""
    where = Path(path)
    if where.name == rekordbox_usb.DB_NAME or where.suffix != ".db":
        return rekordbox_usb.read_usb_cues(where)
    return master_reader(where)
