#!/usr/bin/env python3
"""Regenerate tilt-variant e2e fixtures from public source JPEGs.

Rotates each source receipt by a fixed set of angles (PIL bicubic, white
fill, expand=True) and copies its ``.expected.json`` alongside — tilt only
changes bbox positions, not merchant/total/items. Tilt variants run only in
live e2e mode; no ``.ocr.json`` is generated.

Idempotent: rerunning overwrites the same output paths byte-for-byte.

Usage:
    python scripts/gen_tilt_fixtures.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "receipts_e2e"

SOURCES: tuple[str, ...] = (
    "costco_20260218_redact",
    "tnt_20260217_redact",
)
TILT_ANGLES_DEG: tuple[int, ...] = (3, 5, 7)


def _rotate_jpeg(src: Path, dst: Path, angle_deg: int) -> None:
    with Image.open(src) as img:
        rotated = img.rotate(
            angle_deg,
            resample=Image.Resampling.BICUBIC,
            expand=True,
            fillcolor=(255, 255, 255),
        )
        rotated.convert("RGB").save(dst, format="JPEG", quality=92)


def generate(fixtures_dir: Path = FIXTURES_DIR) -> list[Path]:
    written: list[Path] = []
    for name in SOURCES:
        src_jpg = fixtures_dir / f"{name}.jpg"
        src_expected = fixtures_dir / f"{name}.expected.json"
        if not src_jpg.exists():
            raise FileNotFoundError(src_jpg)
        if not src_expected.exists():
            raise FileNotFoundError(src_expected)
        for angle in TILT_ANGLES_DEG:
            variant = f"{name}_tilt{angle}"
            dst_jpg = fixtures_dir / f"{variant}.jpg"
            dst_expected = fixtures_dir / f"{variant}.expected.json"
            _rotate_jpeg(src_jpg, dst_jpg, angle)
            shutil.copyfile(src_expected, dst_expected)
            written.extend([dst_jpg, dst_expected])
    return written


def main() -> int:
    written = generate()
    for path in written:
        print(path.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
