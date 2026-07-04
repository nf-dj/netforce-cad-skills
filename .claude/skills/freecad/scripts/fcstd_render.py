#!/usr/bin/env python3
"""Render shaded PNG views of a .FCStd model, fully headless (for humans to
check results; agents can read the PNG too).

Usage:
  fcstd_render.py FILE [-o OUT.png|OUT.pdf] [--views iso,top,front,right] [--object NAME ...]

Default renders the PartDesign Bodies (or top-level shapes) in an isometric
view. Multiple --views tile into one image. Uses FreeCAD's bundled
matplotlib — no extra dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fc_common


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("-o", "--out", type=Path,
                    help="output file; .png (default) or .pdf for vector output")
    ap.add_argument("--views", default="iso",
                    help="comma list: iso,top,bottom,front,back,left,right")
    ap.add_argument("--object", action="append", dest="objects",
                    help="render only these objects (repeatable)")
    ap.add_argument("--width", type=int, default=1200, help="image width px")
    args = ap.parse_args()

    f = args.file.resolve()
    if not f.exists():
        sys.exit(f"no such file: {f}")
    out = (args.out or f.with_suffix(".png")).resolve()
    result = fc_common.run_in_freecad("fc_render.py", {
        "file": str(f),
        "out": str(out),
        "objects": args.objects,
        "views": [v.strip() for v in args.views.split(",") if v.strip()],
        "width_px": args.width,
    })
    print(json.dumps(result, indent=1))
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
