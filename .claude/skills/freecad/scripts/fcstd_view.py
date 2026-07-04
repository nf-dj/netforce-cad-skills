#!/usr/bin/env python3
"""Semantic text view of a FreeCAD .FCStd file — stdlib only, no FreeCAD needed.

Usage:
  fcstd_view.py FILE [--object NAME] [--deps] [--all-props] [--json] [--summary] [--deep]

Shows the full object tree (object Name is the stable edit handle), every data
property with type/value/bound expression, sketch geometry + constraints
(constraint Name or index is the `set-datum` handle), and spreadsheet cells
with aliases. Lengths are mm, angles degrees.

--deep additionally runs headless FreeCAD (freecadcmd) to report recompute
state, shape volume/bounding box per object, and sketch solver status.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Sketcher::Constraint Type enum (FreeCAD source: Constraint.h)
CONSTRAINT_TYPES = {
    0: "None", 1: "Coincident", 2: "Horizontal", 3: "Vertical", 4: "Parallel",
    5: "Tangent", 6: "Distance", 7: "DistanceX", 8: "DistanceY", 9: "Angle",
    10: "Perpendicular", 11: "Radius", 12: "Equal", 13: "PointOnObject",
    14: "Symmetric", 15: "InternalAlignment", 16: "SnellsLaw", 17: "Block",
    18: "Diameter", 19: "Weight",
}
VALUE_CONSTRAINTS = {"Distance", "DistanceX", "DistanceY", "Angle", "Radius", "Diameter", "Weight"}

# properties that are pure view/UI noise
NOISE_PROPS = {
    "Label2", "Visibility", "AttacherEngine", "AttachmentOffset", "MapMode",
    "MapPathParameter", "MapReversed", "AttachmentSupport", "ShowHidden",
    "columnWidths", "rowHeights", "InternalShape", "FullyConstrained",
    "Shape", "AddSubShape", "SuppressedShape", "BaseFeature", "_Body",
}


def fnum(v: str) -> float:
    return round(float(v), 6)


# ---------------------------------------------------------------- property parsing

def parse_property(prop: ET.Element):
    """Return a JSON-friendly value for a <Property> node, or None to skip."""
    ptype = prop.get("type", "")
    ch = list(prop)
    if not ch:
        return None
    el = ch[0]
    tag = el.tag
    if tag in ("Float", "Integer", "Bool", "String", "Uuid"):
        v = el.get("value")
        if tag == "Float":
            return fnum(v)
        if tag == "Integer":
            return int(v)
        if tag == "Bool":
            return v == "true"
        return v
    if tag == "PropertyPlacement":
        pos = [fnum(el.get(k)) for k in ("Px", "Py", "Pz")]
        angle = round(math.degrees(float(el.get("A", "0"))), 4)
        axis = [fnum(el.get(k)) for k in ("Ox", "Oy", "Oz")]
        if pos == [0, 0, 0] and angle == 0:
            return "identity"
        return {"position": pos, "axis": axis, "angle_deg": angle}
    if tag == "PropertyVector":
        return [fnum(el.get(k)) for k in ("valueX", "valueY", "valueZ")]
    if tag == "Link":
        return el.get("value") or None
    if tag == "LinkList":
        return [l.get("value") for l in el] or None
    if tag == "LinkSub":
        subs = [s.get("value") for s in el]
        base = el.get("value")
        return f"{base}[{','.join(subs)}]" if subs else base
    if tag == "LinkSubList":
        out = []
        for l in el:
            sub = l.get("sub", "")
            out.append(f"{l.get('obj')}.{sub}" if sub else l.get("obj"))
        return out or None
    if ptype == "App::PropertyEnumeration":
        idx = el.get("value")
        enums = prop.find("CustomEnumList")
        if enums is None:
            return int(idx) if idx is not None else None
        vals = [e.get("value") for e in enums]
        i = int(idx)
        return vals[i] if 0 <= i < len(vals) else i
    return None  # complex/binary property (geometry handled separately)


def parse_expressions(obj_data: ET.Element) -> dict[str, str]:
    out = {}
    for prop in obj_data.iter("Property"):
        if prop.get("name") == "ExpressionEngine":
            for e in prop.iter("Expression"):
                out[e.get("path")] = e.get("expression")
    return out


# ---------------------------------------------------------------- sketch parsing

def parse_geometry(prop: ET.Element) -> list[dict]:
    geoms = []
    for g in prop.iter("Geometry"):
        gtype = g.get("type", "").replace("Part::Geom", "")
        entry = {"index": len(geoms), "type": gtype}
        for el in g.iter():
            if el.tag == "LineSegment":
                entry["from"] = [fnum(el.get("StartX")), fnum(el.get("StartY"))]
                entry["to"] = [fnum(el.get("EndX")), fnum(el.get("EndY"))]
            elif el.tag in ("Circle", "ArcOfCircle"):
                entry["center"] = [fnum(el.get("CenterX")), fnum(el.get("CenterY"))]
                entry["radius"] = fnum(el.get("Radius"))
                if el.get("StartAngle"):
                    entry["start_deg"] = round(math.degrees(float(el.get("StartAngle"))), 3)
                    entry["end_deg"] = round(math.degrees(float(el.get("EndAngle"))), 3)
            elif el.tag == "Point":
                entry["at"] = [fnum(el.get("X")), fnum(el.get("Y"))]
            elif el.tag == "Construction" or (el.tag == "GeoExtension" and el.get("geometryModeFlags")):
                flags = el.get("geometryModeFlags", "")
                if flags and flags[-1] == "1":  # bit 0 = construction
                    entry["construction"] = True
        geoms.append(entry)
    return geoms


GEO_REF = {-1: "RootPoint", -2: "X-Axis", -3: "Y-Axis", -2000: ""}
POS_NAME = {0: "", 1: "start", 2: "end", 3: "center"}


def geo_ref(idx: int, pos: int) -> str:
    base = GEO_REF.get(idx, f"g{idx}")
    if not base:
        return ""
    p = POS_NAME.get(pos, str(pos))
    return f"{base}.{p}" if p else base


def parse_constraints(prop: ET.Element) -> list[dict]:
    out = []
    for i, c in enumerate(prop.iter("Constrain")):
        ctype = CONSTRAINT_TYPES.get(int(c.get("Type", "0")), c.get("Type"))
        entry = {"index": i, "type": ctype}
        if c.get("Name"):
            entry["name"] = c.get("Name")
        refs = [geo_ref(int(c.get(k, "-2000")), int(c.get(k + "Pos", "0")))
                for k in ("First", "Second", "Third")]
        entry["refs"] = [r for r in refs if r]
        if ctype in VALUE_CONSTRAINTS:
            v = float(c.get("Value", "0"))
            entry["value"] = round(math.degrees(v), 4) if ctype == "Angle" else round(v, 4)
            entry["driving"] = c.get("IsDriving", "1") == "1"
        out.append(entry)
    return out


def parse_cells(prop: ET.Element) -> dict:
    cells, aliases = {}, {}
    for cell in prop.iter("Cell"):
        addr = cell.get("address")
        content = cell.get("content", "")
        cells[addr] = content
        if cell.get("alias"):
            aliases[cell.get("alias")] = addr
    return {"cells": cells, "aliases": aliases}


# ---------------------------------------------------------------- document model

def fcstd_model(path: Path, all_props: bool) -> dict:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("Document.xml"))

    doc_label = ""
    for p in root.find("Properties").iter("Property"):
        if p.get("name") == "Label":
            doc_label = p.find("String").get("value")

    objects_sec = root.find("Objects")
    deps = {}
    for od in objects_sec.iter("ObjectDeps"):
        deps[od.get("Name")] = sorted({d.get("Name") for d in od})

    types = {o.get("name"): o.get("type") for o in objects_sec.iter("Object")}

    objects = []
    objectdata = root.find("ObjectData")
    for obj in objectdata.iter("Object"):
        name = obj.get("name")
        entry = {"name": name, "type": types.get(name, "?")}
        props, sketch, sheet = {}, None, None
        for prop in obj.find("Properties").iter("Property"):
            pname = prop.get("name")
            ptype = prop.get("type", "")
            if pname == "Label":
                lbl = prop.find("String")
                if lbl is not None and lbl.get("value") != name:
                    entry["label"] = lbl.get("value")
                continue
            if ptype == "Part::PropertyGeometryList":
                sketch = sketch or {}
                sketch["geometry"] = parse_geometry(prop)
                continue
            if ptype == "Sketcher::PropertyConstraintList":
                sketch = sketch or {}
                sketch["constraints"] = parse_constraints(prop)
                continue
            if ptype == "Spreadsheet::PropertySheet":
                sheet = parse_cells(prop)
                continue
            if pname == "ExpressionEngine":
                continue
            if not all_props and pname in NOISE_PROPS:
                continue
            val = parse_property(prop)
            if val is not None:
                props[pname] = val
        exprs = parse_expressions(obj)
        if props:
            entry["properties"] = props
        if exprs:
            entry["expressions"] = exprs
        if sketch:
            entry["sketch"] = sketch
        if sheet:
            entry["spreadsheet"] = sheet
        if deps.get(name):
            entry["depends_on"] = deps[name]
        objects.append(entry)

    return {
        "file": str(path),
        "type": "FCStd",
        "program_version": root.get("ProgramVersion"),
        "label": doc_label,
        "objects": objects,
    }


# ---------------------------------------------------------------- deep inspect

def run_deep(path: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    import fc_common
    return fc_common.run_in_freecad("fc_inspect.py", {"file": str(path.resolve())})


# ---------------------------------------------------------------- markdown

def fmt_val(v) -> str:
    if isinstance(v, dict):
        return json.dumps(v)
    if isinstance(v, list):
        return json.dumps(v)
    return str(v)


def markdown(m: dict, summary: bool, deep: dict | None) -> str:
    L = [f"# FreeCAD: {Path(m['file']).name}"]
    L.append(f"document label: {m['label']} | saved by FreeCAD {m['program_version']}")
    L.append("Object **Name** (not Label) is the handle for edit commands. Lengths mm, angles deg.")

    skip_types = {"App::Origin", "App::Line", "App::Plane"} if summary else set()
    L.append(f"\n## Objects ({len(m['objects'])})")
    for o in m["objects"]:
        if o["type"] in skip_types:
            continue
        lbl = f' (label "{o["label"]}")' if o.get("label") else ""
        L.append(f"\n### {o['name']} — {o['type']}{lbl}")
        if o.get("depends_on"):
            L.append(f"depends on: {', '.join(o['depends_on'])}")
        if o.get("properties") and not (summary and o["type"].startswith("App::")):
            rows = [f"| {k} | {fmt_val(v)} |" for k, v in sorted(o["properties"].items())]
            L.append("| property | value |")
            L.append("|---|---|")
            L.extend(rows)
        if o.get("expressions"):
            for path, expr in o["expressions"].items():
                L.append(f"- expression: `{o['name']}.{path} = {expr}`")
        sk = o.get("sketch")
        if sk and not summary:
            if sk.get("geometry"):
                L.append(f"\n**Sketch geometry** ({len(sk['geometry'])} elements):")
                L.append("| # | type | detail |")
                L.append("|---|---|---|")
                for g in sk["geometry"]:
                    det = {k: v for k, v in g.items() if k not in ("index", "type")}
                    L.append(f"| g{g['index']} | {g['type']}"
                             f"{' (construction)' if g.get('construction') else ''} | "
                             f"{json.dumps(det) if det else ''} |")
            if sk.get("constraints"):
                L.append(f"\n**Sketch constraints** ({len(sk['constraints'])}):")
                L.append("| # | name | type | refs | value | driving |")
                L.append("|---|---|---|---|---|---|")
                for c in sk["constraints"]:
                    L.append(f"| {c['index']} | {c.get('name', '')} | {c['type']} | "
                             f"{','.join(c['refs'])} | {c.get('value', '')} | "
                             f"{'yes' if c.get('driving') else ('no' if 'driving' in c else '')} |")
        sh = o.get("spreadsheet")
        if sh:
            L.append("\n**Spreadsheet cells**:")
            L.append("| cell | content | alias |")
            L.append("|---|---|---|")
            alias_by_addr = {a: n for n, a in sh["aliases"].items()}
            for addr in sorted(sh["cells"], key=lambda a: (re.sub(r"\d", "", a), int(re.sub(r"\D", "", a) or 0))):
                L.append(f"| {addr} | {sh['cells'][addr]} | {alias_by_addr.get(addr, '')} |")

    if deep:
        L.append("\n## Recompute state (headless FreeCAD)")
        if deep.get("errors"):
            for e in deep["errors"]:
                L.append(f"- ERROR {e}")
        for name, info in sorted(deep.get("objects", {}).items()):
            parts = []
            if info.get("volume") is not None:
                parts.append(f"volume {info['volume']:.3f} mm³")
            if info.get("bbox"):
                parts.append(f"bbox {info['bbox']}")
            if info.get("solver") is not None:
                dof = f", {info['dof']} DoF" if info.get("dof") is not None else ""
                parts.append(f"solver rc {info['solver']}{dof}, "
                             f"{'fully constrained' if info.get('fully_constrained') else 'NOT fully constrained'}")
            if info.get("state"):
                parts.append(f"state {info['state']}")
            if parts:
                L.append(f"- **{name}**: {'; '.join(parts)}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--object", help="show only this object (by Name)")
    ap.add_argument("--deps", action="store_true", help="(kept in JSON always)")
    ap.add_argument("--all-props", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--deep", action="store_true", help="add recompute/volume/solver info via freecadcmd")
    args = ap.parse_args()

    if not args.file.exists():
        sys.exit(f"no such file: {args.file}")
    m = fcstd_model(args.file, args.all_props)
    if args.object:
        m["objects"] = [o for o in m["objects"] if o["name"] == args.object]
        if not m["objects"]:
            sys.exit(f"no object named {args.object!r} (names are case-sensitive)")

    deep = None
    if args.deep:
        deep = run_deep(args.file)
        m["deep"] = deep

    if args.json:
        print(json.dumps(m, indent=1))
    else:
        print(markdown(m, args.summary, deep))


if __name__ == "__main__":
    main()
