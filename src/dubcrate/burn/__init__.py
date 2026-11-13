import sys

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
    "backend_problem",
    "default_backend",
    "get_burner",
]

BACKENDS = ("discrecording", "cdrdao")


def default_backend() -> str:
    """The backend to burn with on this computer: Apple's DiscRecording on a Mac, `cdrdao`
    elsewhere. The one place that decides; callers never test the platform."""
    return "discrecording" if sys.platform == "darwin" else "cdrdao"


def backend_problem(name: str) -> str | None:
    """Why backend `name` can't burn on this computer, in words for the user, or None if it can
    be tried (that says nothing about a drive or a disc)."""
    if name == "discrecording":
        if sys.platform != "darwin":
            return "Burning with Apple's DiscRecording only works on a Mac."
        return None
    if name == "cdrdao":
        from dubcrate.burn import cdrdao

        if cdrdao.find_cdrdao() is None:
            return f"Dubcrate burns with cdrdao here, and it was not found. {cdrdao.install_hint()}, then restart Dubcrate."
        return None
    return f"Unknown burn backend {name!r}."


def get_burner(name: str) -> Burner:
    """Return the burn backend called `name` ("discrecording" or "cdrdao")."""
    if name == "discrecording":
        from dubcrate.burn.discrecording import DiscRecordingBurner

        return DiscRecordingBurner()
    if name == "cdrdao":
        from dubcrate.burn.cdrdao import CdrdaoBurner

        return CdrdaoBurner()
    raise BurnError(f"unknown burn backend {name!r}")
