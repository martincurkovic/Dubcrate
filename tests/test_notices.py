"""Every Python package the app ships must be named in the third-party notices."""

import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
NOTICES = (ROOT / "src/dubcrate/ui/help/THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8").lower()  # fmt: skip

# Named in the notices as one group (the PyObjC packages), or part of Qt for Python.
COVERED_BY = {
    "pyobjc-core": "pyobjc",
    "pyobjc-framework-cocoa": "pyobjc",
    "pyobjc-framework-discrecording": "pyobjc",
    "pyside6-essentials": "pyside6",
    "shiboken6": "pyside6",
}


def runtime_closure() -> set[str]:
    """The packages the app needs to run (not the tools that test or build it), with theirs."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(requirement: str) -> None:
        req = Requirement(requirement)
        name = req.name.lower().replace("_", "-")
        if name in found or (req.marker and not req.marker.evaluate({"extra": ""})):
            return
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            return  # (not installed on this platform)
        found.add(name)
        for dependency in dist.requires or []:
            walk(dependency)

    for requirement in project["project"]["dependencies"]:
        walk(requirement)
    return found


def test_every_package_the_app_ships_is_in_the_notices():
    closure = runtime_closure()
    assert {
        "miniaudio",
        "numpy",
        "pyrekordbox",
        "certifi",
    } <= closure  # (the walk works)
    missing = [
        name
        for name in sorted(closure)
        if COVERED_BY.get(name, name).lower() not in NOTICES
    ]
    assert not missing, f"add these to THIRD_PARTY_NOTICES.md: {missing}"


def test_the_notices_name_what_is_not_a_python_package():
    for needed in ("qt", "chikarego2", "cc by", "ffmpeg", "cdrdao", "nuitka", "sqlcipher", "deep symmetry"):  # fmt: skip
        assert needed in NOTICES, needed
