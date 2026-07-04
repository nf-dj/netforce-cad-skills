"""Runs INSIDE freecadcmd: open a document, recompute, report per-object
shape stats, sketch solver state, and errors. Read-only (never saves).

Input (FC_SKILL_ARGS json): {"file": "/abs/path.FCStd"}
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_common

import FreeCAD as App


def main():
    args = fc_common.read_args()
    result = {"objects": {}, "errors": []}
    try:
        doc = App.openDocument(args["file"])
    except Exception as e:
        fc_common.emit({"errors": [f"open failed: {e}"], "objects": {}})
        return

    try:
        doc.recompute()
    except Exception as e:
        result["errors"].append(f"recompute raised: {e}")

    SKIP_TYPES = ("App::Origin", "App::Line", "App::Plane")
    for obj in doc.Objects:
        if obj.TypeId in SKIP_TYPES:
            continue
        info = {}
        state = [s for s in obj.State if s not in ("Up-to-date",)]
        if state:
            info["state"] = [str(s) for s in state]
            if "Invalid" in str(state) or "Error" in str(state):
                result["errors"].append(f"{obj.Name}: {state}")
        if hasattr(obj, "Shape"):
            try:
                sh = obj.Shape
                if not sh.isNull():
                    bb = sh.BoundBox
                    if all(abs(v) < 1e50 for v in (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax)):
                        info["volume"] = round(sh.Volume, 6)
                        info["bbox"] = [round(v, 4) for v in
                                        (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)]
            except Exception as e:
                info["shape_error"] = str(e)
        if obj.TypeId == "Sketcher::SketchObject":
            try:
                info["solver"] = obj.solve()
            except Exception as e:
                info["solver_error"] = str(e)
            try:
                info["dof"] = int(obj.getDoFs()) if hasattr(obj, "getDoFs") else None
            except Exception:
                info["dof"] = None
            info["fully_constrained"] = bool(getattr(obj, "FullyConstrained", False))
        if info:
            result["objects"][obj.Name] = info

    fc_common.emit(result)


main()
