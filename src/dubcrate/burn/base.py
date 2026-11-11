"""Backend-neutral description of a disc and the interface burners implement."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from dubcrate.cd import DISC_80_SECTORS, disc_used_sectors


class BurnError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscTrack:
    """One rendered track: a CD-ready WAV whose length is whole sectors."""

    wav: Path
    title: str
    performer: str
    sectors: int


@dataclass
class Disc:
    title: str
    performer: str
    tracks: list[DiscTrack] = field(default_factory=list)

    @property
    def total_sectors(self) -> int:
        return sum(t.sectors for t in self.tracks)

    def used_sectors(self, pregap_sectors: int = 0) -> int:
        """Sectors the disc consumes, counting silence before tracks 2 and up."""
        return disc_used_sectors((t.sectors for t in self.tracks), pregap_sectors)

    def fits(self, capacity: int = DISC_80_SECTORS, pregap_sectors: int = 0) -> bool:
        return self.used_sectors(pregap_sectors) <= capacity


@dataclass(frozen=True)
class BurnOptions:
    speed: int | None = 10  # CD "x" factor; None means the drive's maximum
    simulate: bool = False  # laser-off test write, if the drive supports it
    eject: bool = True
    verify: bool = False
    # Silence inserted before tracks 2 and up, in sectors (75 = 1 s). 0 is
    # gapless; the Red Book default of 150 is the 2 s gap you'd get otherwise.
    pregap_sectors: int = 0
    device: str | None = None  # backend-specific device name; None = first drive


Progress = Callable[[str], None]


class Burner(Protocol):
    name: str

    def burn(self, disc: Disc, options: BurnOptions, progress: Progress) -> None: ...
