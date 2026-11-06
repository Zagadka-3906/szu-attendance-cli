"""生成一张纯黑签到照片（所有课通用）。

python scripts/make_blank_photo.py [-o photo.jpg] [--size 1080x1440]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="photo.jpg")
    ap.add_argument("--size", default="1080x1440")
    ap.add_argument("--quality", type=int, default=90)
    a = ap.parse_args()

    w, h = (int(x) for x in a.size.lower().split("x"))
    img = Image.new("RGB", (w, h), (0, 0, 0))
    out = Path(a.out)
    img.save(out, "JPEG", quality=a.quality, optimize=True)
    print(f"[+] {out.resolve()}  {w}x{h}  {out.stat().st_size/1024:.1f} KB  纯黑 RGB(0,0,0)")


if __name__ == "__main__":
    main()
