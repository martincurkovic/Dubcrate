from dubcrate.burn.base import (
    Burner,
    BurnError,
    BurnOptions,
    DataDisc,
    Disc,
    DiscTrack,
    MediaInfo,
)

__all__ = [
    "BurnError",
    "BurnOptions",
    "Burner",
    "DataDisc",
    "Disc",
    "DiscTrack",
    "MediaInfo",
    "get_burner",
]


def get_burner(name: str) -> Burner:
    """Return the burn backend called `name` ("discrecording" or "cdrdao")."""
    if name == "discrecording":
        from dubcrate.burn.discrecording import DiscRecordingBurner

        return DiscRecordingBurner()
    if name == "cdrdao":
        from dubcrate.burn.cdrdao import CdrdaoBurner

        return CdrdaoBurner()
    raise BurnError(f"unknown burn backend {name!r}")
