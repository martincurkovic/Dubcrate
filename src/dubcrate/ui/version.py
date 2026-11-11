"""What the app says about itself: its version (splash screen, About) and its icon."""

from importlib import metadata
from pathlib import Path


def app_version() -> str:
    """A built app: the version the build script took from the nearest git tag. From a checkout:
    the installed package's, else "development"."""
    try:
        from dubcrate._build_version import (
            APP_VERSION,
        )  # written by scripts/build_*; not tracked

        return APP_VERSION
    except ImportError:
        pass
    try:
        return metadata.version("dubcrate")
    except metadata.PackageNotFoundError:
        return "development"


def icon_path() -> Path | None:
    """The app icon: beside this file in a built app (the build copies it there), else from
    a checkout's assets/icon."""
    for candidate in (
        Path(__file__).parent / "AppIcon.png",
        Path(__file__).resolve().parents[3] / "assets" / "icon" / "AppIcon.png",
    ):
        if candidate.is_file():
            return candidate
    return None
