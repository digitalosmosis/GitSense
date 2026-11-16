"""Render src/gitsense/assets/icon.svg to GitSense.icns and icon-512.png.

Usage: python tools/build_icon.py

- Needs rsvg-convert (`brew install librsvg`) and iconutil (part of macOS).
- Writes src/gitsense/assets/GitSense.icns and src/gitsense/assets/icon-512.png.
- Both outputs are committed, so installing GitSense never needs these tools.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "src/gitsense/assets"
SOURCE = ASSETS / "icon.svg"
ICNS = ASSETS / "GitSense.icns"
PNG_512 = ASSETS / "icon-512.png"

# Point sizes an .iconset holds; each comes at 1x and 2x.
SIZES = (16, 32, 128, 256, 512)

RSVG_CANDIDATES = ("/opt/homebrew/bin/rsvg-convert", "/usr/local/bin/rsvg-convert")


def find_rsvg() -> str | None:
    """Locate rsvg-convert on PATH or in the usual Homebrew folders."""
    found = shutil.which("rsvg-convert")
    if found:
        return found
    return next((path for path in RSVG_CANDIDATES if Path(path).is_file()), None)


def render(rsvg: str, size: int, out: Path) -> None:
    """Render the artwork as a square PNG of the given pixel size."""
    subprocess.run(
        [rsvg, "--width", str(size), "--height", str(size), "--format", "png", "--output", str(out), str(SOURCE)],
        check=True,
    )


def main() -> int:
    if not SOURCE.is_file():
        print(f"build_icon: {SOURCE} not found", file=sys.stderr)
        return 1
    rsvg = find_rsvg()
    if rsvg is None:
        print("build_icon: rsvg-convert not found. Install it with: brew install librsvg", file=sys.stderr)
        return 1
    iconutil = shutil.which("iconutil") or "/usr/bin/iconutil"
    if not Path(iconutil).is_file():
        print("build_icon: iconutil not found. It ships with macOS, so run this on a Mac.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "GitSense.iconset"
        iconset.mkdir()
        for size in SIZES:
            render(rsvg, size, iconset / f"icon_{size}x{size}.png")
            render(rsvg, size * 2, iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run([iconutil, "--convert", "icns", "--output", str(ICNS), str(iconset)], check=True)
        shutil.copyfile(iconset / "icon_512x512.png", PNG_512)

    print(f"Wrote {ICNS.relative_to(ASSETS.parent.parent.parent)}")
    print(f"Wrote {PNG_512.relative_to(ASSETS.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
