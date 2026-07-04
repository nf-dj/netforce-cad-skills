"""Runs INSIDE freecadcmd: tessellate solids and render shaded PNG views with
the bundled matplotlib (Agg backend — fully headless). Read-only.

Input (FC_SKILL_ARGS json):
  {"file": "...", "out": "/abs/out.png", "objects": ["Body"]|null,
   "views": ["iso","top","front","right"], "width_px": 1200}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_common

import FreeCAD as App

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np

SKIP_TYPES = ("App::Origin", "App::Line", "App::Plane")
COLORS = ["#7a9cc6", "#c6a97a", "#8fc67a", "#c67a8f", "#7ac6bb", "#a97ac6"]

VIEWS = {  # (elev, azim)
    "iso": (30, -60),
    "top": (90, -90),
    "bottom": (-90, -90),
    "front": (0, -90),
    "back": (0, 90),
    "right": (0, 0),
    "left": (0, 180),
}


def pick_objects(doc, wanted):
    if wanted:
        objs = []
        for name in wanted:
            o = doc.getObject(name)
            if o is None:
                raise ValueError(f"no object named {name!r}")
            objs.append(o)
        return objs
    # default: Bodies; else top-level shapes nothing depends on
    bodies = [o for o in doc.Objects if o.TypeId == "PartDesign::Body"]
    if bodies:
        return bodies
    cands = [o for o in doc.Objects
             if o.TypeId not in SKIP_TYPES and hasattr(o, "Shape")
             and o.TypeId != "Sketcher::SketchObject" and not o.Shape.isNull()]
    roots = [o for o in cands if not any(o in c.OutList for c in cands if c is not o)]
    return roots or cands


def main():
    args = fc_common.read_args()
    doc = App.openDocument(args["file"])
    doc.recompute()

    objs = pick_objects(doc, args.get("objects"))
    views = args.get("views") or ["iso"]
    meshes = []  # (name, color, verts, faces)
    for i, o in enumerate(objs):
        if not hasattr(o, "Shape") or o.Shape.isNull():
            continue
        verts, faces = o.Shape.tessellate(0.3)
        if not faces:
            continue
        v = np.array([[p.x, p.y, p.z] for p in verts])
        meshes.append((o.Name, COLORS[i % len(COLORS)], v, faces))
    if not meshes:
        fc_common.emit({"error": "nothing to render (no non-empty shapes)"})
        return

    allv = np.vstack([v for _, _, v, _ in meshes])
    lo, hi = allv.min(axis=0), allv.max(axis=0)
    center, span = (lo + hi) / 2, max((hi - lo).max(), 1e-6)

    n = len(views)
    cols = 2 if n > 1 else 1
    rows = (n + cols - 1) // cols
    wpx = args.get("width_px", 1200)
    fig = plt.figure(figsize=(wpx / 100, wpx / 100 * rows / cols * 0.9), dpi=100)
    for vi, view in enumerate(views):
        if view not in VIEWS:
            fc_common.emit({"error": f"unknown view {view!r}; options: {sorted(VIEWS)}"})
            return
        ax = fig.add_subplot(rows, cols, vi + 1, projection="3d")
        elev, azim = VIEWS[view]
        light = np.array([0.4, -0.5, 0.77])
        light = light / np.linalg.norm(light)
        for name, color, v, faces in meshes:
            tris = v[np.array([f[:3] for f in faces])]
            # manual lambertian shading per triangle
            n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
            norm = np.linalg.norm(n, axis=1, keepdims=True)
            n = np.divide(n, norm, out=np.zeros_like(n), where=norm > 0)
            lum = 0.45 + 0.55 * np.abs(n @ light)
            base = np.array(matplotlib.colors.to_rgb(color))
            fc = np.clip(base[None, :] * lum[:, None], 0, 1)
            pc = Poly3DCollection(tris, facecolors=fc, edgecolors="none")
            ax.add_collection3d(pc)
        ax.set_xlim(center[0] - span / 2, center[0] + span / 2)
        ax.set_ylim(center[1] - span / 2, center[1] + span / 2)
        ax.set_zlim(center[2] - span / 2, center[2] + span / 2)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(f"{view} (mm)", fontsize=9)
        ax.tick_params(labelsize=6)
    fig.suptitle(", ".join(m[0] for m in meshes), fontsize=10)
    fig.tight_layout()
    fig.savefig(args["out"])

    fc_common.emit({"rendered": args["out"], "objects": [m[0] for m in meshes],
                    "views": views,
                    "bbox_mm": [round(x, 3) for x in (*lo, *hi)]})


main()
