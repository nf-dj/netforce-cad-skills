"""Runs INSIDE freecadcmd: apply edit ops to a document, recompute once,
save ONLY if the recompute is clean (else leave the file untouched).

Input (FC_SKILL_ARGS json):
  {"file": "/abs/path.FCStd",          # omit or nonexistent + create=true -> new doc
   "create": false,
   "template": "partdesign" | "empty",
   "script": "/abs/build.py",          # optional: run this python against the doc
   "ops": [                            # optional: structured ops, applied in order
     {"op": "set-datum",    "object": "Sketch", "constraint": "boxWidth"|7, "value": "25 mm"},
     {"op": "set-property", "object": "Pad", "property": "Length", "value": "15 mm"},
     {"op": "set-expression","object": "Pad", "property": "Length", "expression": "Params.B2"},
     {"op": "set-cell",     "object": "Params", "cell": "B2", "value": "42"},
     {"op": "set-alias",    "object": "Params", "cell": "B2", "alias": "wall"},
   ],
   "force_save": false, "save": true}

Output: per-op status, per-object recompute errors, before/after volume+bbox.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_common

import FreeCAD as App

SKIP_TYPES = ("App::Origin", "App::Line", "App::Plane")


def shape_stats(doc):
    stats = {}
    for obj in doc.Objects:
        if obj.TypeId in SKIP_TYPES or not hasattr(obj, "Shape"):
            continue
        try:
            sh = obj.Shape
            if sh.isNull():
                continue
            bb = sh.BoundBox
            if all(abs(v) < 1e50 for v in (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax)):
                stats[obj.Name] = {
                    "volume": round(sh.Volume, 6),
                    "bbox": [round(v, 4) for v in
                             (bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)],
                }
        except Exception:
            pass
    return stats


def get_object(doc, name):
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"no object named {name!r} (use fcstd_view.py to list Names)")
    return obj


def quantity(value):
    if isinstance(value, (int, float)):
        return float(value)
    return App.Units.Quantity(str(value))


def apply_op(doc, op):
    kind = op["op"]
    obj = get_object(doc, op["object"])
    if kind == "set-datum":
        c = op["constraint"]
        try:
            c = int(c)
        except (ValueError, TypeError):
            pass  # keep name
        if isinstance(c, str):
            names = [x.Name for x in obj.Constraints]
            if c not in names:
                named = [n for n in names if n]
                raise ValueError(f"no constraint named {c!r}; named constraints: {named}")
            c = names.index(c)
        before = obj.getDatum(c)
        obj.setDatum(c, quantity(op["value"]))
        return {"before": str(before), "after": str(op["value"])}
    if kind == "set-property":
        pname = op["property"]
        if not hasattr(obj, pname):
            raise ValueError(f"{op['object']} has no property {pname!r}")
        before = getattr(obj, pname)
        raw = op["value"]
        # coerce to the property's current type
        if isinstance(before, bool):
            val = str(raw).strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(before, int):
            val = int(raw)
        elif isinstance(before, float):
            val = float(App.Units.Quantity(str(raw)).Value)
        elif hasattr(before, "Value"):  # Quantity (Length/Angle/...)
            val = App.Units.Quantity(str(raw))
        else:
            val = raw
        setattr(obj, pname, val)
        return {"before": str(before), "after": str(raw)}
    if kind == "set-expression":
        obj.setExpression(op["property"], op["expression"] or None)
        return {"after": op["expression"]}
    if kind == "set-cell":
        obj.set(op["cell"], str(op["value"]))
        return {"cell": op["cell"], "after": str(op["value"])}
    if kind == "set-alias":
        obj.setAlias(op["cell"], op["alias"] or "")
        return {"cell": op["cell"], "alias": op["alias"]}
    raise ValueError(f"unknown op {kind!r}")


def main():
    args = fc_common.read_args()
    result = {"ops": [], "errors": [], "saved": False}
    path = args.get("file")

    try:
        if args.get("create"):
            if path and os.path.exists(path):
                fc_common.emit({"errors": [f"refusing to overwrite existing {path}"], "ops": [],
                                "saved": False})
                return
            doc = App.newDocument("new")
            if args.get("template") == "partdesign":
                doc.addObject("PartDesign::Body", "Body")
        else:
            doc = App.openDocument(path)
    except Exception as e:
        fc_common.emit({"errors": [f"open failed: {e}"], "ops": [], "saved": False})
        return

    pre = shape_stats(doc)

    for op in args.get("ops", []):
        entry = {"op": op}
        try:
            entry["result"] = apply_op(doc, op)
            entry["ok"] = True
        except Exception as e:
            entry["ok"] = False
            entry["error"] = str(e)
            result["errors"].append(f"{op.get('op')} {op.get('object')}: {e}")
        result["ops"].append(entry)

    if args.get("script"):
        try:
            src = open(args["script"]).read()
            g = {"App": App, "FreeCAD": App, "doc": doc, "__name__": "__fc_build__"}
            exec(compile(src, args["script"], "exec"), g)
            result["ops"].append({"op": {"op": "run-script", "script": args["script"]}, "ok": True})
        except Exception:
            tb = traceback.format_exc(limit=8)
            result["ops"].append({"op": {"op": "run-script"}, "ok": False, "error": tb})
            result["errors"].append(f"script raised:\n{tb}")

    try:
        doc.recompute()
    except Exception as e:
        result["errors"].append(f"recompute raised: {e}")

    for obj in doc.Objects:
        bad = [str(s) for s in obj.State if str(s) in ("Invalid", "Error")]
        if bad:
            result["errors"].append(f"{obj.Name}: recompute state {bad}")
        if obj.TypeId == "Sketcher::SketchObject":
            try:
                rc = obj.solve()
                if rc != 0:
                    result["errors"].append(f"{obj.Name}: sketch solver failed rc={rc}")
            except Exception as e:
                result["errors"].append(f"{obj.Name}: solver raised {e}")

    post = shape_stats(doc)
    deltas = {}
    warnings = []
    for name in sorted(set(pre) | set(post)):
        a, b = pre.get(name), post.get(name)
        if a != b:
            deltas[name] = {"before": a, "after": b}
        if a and a.get("volume", 0) > 1e-9 and (not b or b.get("volume", 0) <= 1e-9):
            warnings.append(f"{name}: solid collapsed to zero volume — likely degenerate "
                            f"geometry; restore from backup if unintended")
    result["shape_changes"] = deltas
    result["shapes"] = post
    if warnings:
        result["warnings"] = warnings

    clean = not result["errors"]
    if args.get("save", True) and (clean or args.get("force_save")):
        try:
            if args.get("create"):
                doc.saveAs(os.path.abspath(path))
            else:
                doc.save()
            result["saved"] = True
        except Exception as e:
            result["errors"].append(f"save failed: {e}")
    elif not clean:
        result["note"] = "recompute had errors -> file NOT saved (use force_save to override)"

    fc_common.emit(result)


main()
