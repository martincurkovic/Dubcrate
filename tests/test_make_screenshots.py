# scripts/make_screenshots.py: every picture the guide shows has a recipe, and every recipe's picture is in the repo.

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

MANUAL = REPO / "src" / "dubcrate" / "ui" / "help" / "manual.md"
README = REPO / "README.md"
PICTURES = REPO / "src" / "dubcrate" / "ui" / "help" / "screenshots"


def recipes():
    import screenshot_recipes  # (it imports no Qt and no Dubcrate module at the top: the recipes do that when run)

    return screenshot_recipes.SHOTS


def named(path):
    return set(
        re.findall(r"screenshots/([\w-]+)\.png", path.read_text(encoding="utf-8"))
    )


def test_every_picture_the_guide_mentions_has_a_recipe():
    mentioned = named(MANUAL)
    assert mentioned, "found no screenshots in manual.md"
    assert sorted(mentioned - set(recipes())) == []


def test_every_recipe_has_its_picture_in_the_repo():
    assert [n for n in recipes() if not (PICTURES / f"{n}.png").is_file()] == []


def test_the_folder_carries_no_picture_nothing_names():
    used = named(MANUAL) | named(README)
    on_disk = {p.stem for p in PICTURES.glob("*.png")}
    assert on_disk == used, (
        f"unused: {sorted(on_disk - used)}, missing: {sorted(used - on_disk)}"
    )


def test_every_picture_the_readme_shows_exists():
    for name in named(README):
        assert (PICTURES / f"{name}.png").is_file(), name
