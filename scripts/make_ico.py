"""Make the Windows icon (assets/icon/Dubcrate.ico) from the 1024 px master (assets/icon/AppIcon.png).

The macOS build makes an .icns from the same master (scripts/make_icons.sh). The .ico is not tracked: it is made on every
Windows build, and holds the sizes Windows asks for (the taskbar, the title bar, the installer, the Start menu).
Needs Pillow (the `build` dependency group).

    uv run python scripts/make_ico.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "icon" / "AppIcon.png"
ICON = ROOT / "assets" / "icon" / "Dubcrate.ico"
SIZES = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]


def main() -> None:
    with Image.open(MASTER) as image:
        image.convert("RGBA").save(ICON, format="ICO", sizes=SIZES)
    print(f"Wrote {ICON} ({', '.join(str(s[0]) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
