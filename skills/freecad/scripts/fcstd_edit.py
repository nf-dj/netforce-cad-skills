#!/usr/bin/env python3
"""Parametric edits and creation for FreeCAD .FCStd files, fully headless.

Usage:
  fcstd_edit.py set-datum FILE OBJECT CONSTRAINT VALUE   # by name or index: boxWidth | 9; "25 mm"
  fcstd_edit.py set-property FILE OBJECT PROPERTY VALUE
  fcstd_edit.py set-expression FILE OBJECT PROPERTY EXPR   # EXPR='' clears the binding
  fcstd_edit.py set-cell FILE OBJECT CELL VALUE            # spreadsheet, e.g. B2 42 or B2 '=A1*2'
  fcstd_edit.py set-alias FILE OBJECT CELL ALIAS
  fcstd_edit.py batch FILE OPS.json                        # list of op dicts, ONE recompute
  fcstd_edit.py recompute FILE                             # verify-only (never saves)
  fcstd_edit.py new FILE [--template partdesign|empty]
  fcstd_edit.py run-script FILE BUILD.py [--new] [--force-save]

Get handles (object Name, constraint name/index, cell address) from
fcstd_view.py. Values with units: "25 mm", "12 deg"; bare numbers are mm/deg.

Every edit: backup -> apply in headless FreeCAD -> ONE recompute -> save ONLY
if recompute is clean (per-object Invalid state + sketch solver checked);
otherwise the file is left untouched and errors are reported. Report is JSON
on stdout; exits non-zero if any op or the recompute failed.

run-script executes BUILD.py inside FreeCAD with globals: App/FreeCAD (the
module) and doc (the open document). Use it for from-scratch geometry
(sketches, constraints, PartDesign pads/pockets, primitives, booleans).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fc_common


def run(payload: dict, file: Path, is_new: bool) -> None:
    bak = None
    if file.exists() and not is_new:
        bak = fc_common.backup(file)
    result = fc_common.run_in_freecad("fc_ops.py", payload)
    if bak is not None:
        result["backup"] = str(bak)
    print(json.dumps(result, indent=1))
    if result.get("errors") or (payload.get("save", True) and not result.get("saved")):
        sys.exit(1)


def single_op(args, op: dict) -> None:
    f = args.file.resolve()
    if not f.exists():
        sys.exit(f"no such file: {f}")
    run({"file": str(f), "ops": [op]}, f, is_new=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p_with(name, *pos):
        p = sub.add_parser(name)
        p.add_argument("file", type=Path)
        for a in pos:
            p.add_argument(a)
        return p

    p_with("set-datum", "object", "constraint", "value")
    p_with("set-property", "object", "property", "value")
    p_with("set-expression", "object", "property", "expression")
    p_with("set-cell", "object", "cell", "value")
    p_with("set-alias", "object", "cell", "alias")
    p_with("batch", "ops_json")
    p_with("recompute")
    p = p_with("new")
    p.add_argument("--template", choices=["partdesign", "empty"], default="partdesign")
    p = p_with("run-script", "script")
    p.add_argument("--new", action="store_true", help="create FILE instead of opening it")
    p.add_argument("--template", choices=["partdesign", "empty"], default="empty")
    p.add_argument("--force-save", action="store_true")

    args = ap.parse_args()
    f = args.file.resolve()

    if args.cmd == "set-datum":
        single_op(args, {"op": "set-datum", "object": args.object,
                         "constraint": args.constraint, "value": args.value})
    elif args.cmd == "set-property":
        single_op(args, {"op": "set-property", "object": args.object,
                         "property": args.property, "value": args.value})
    elif args.cmd == "set-expression":
        single_op(args, {"op": "set-expression", "object": args.object,
                         "property": args.property, "expression": args.expression})
    elif args.cmd == "set-cell":
        single_op(args, {"op": "set-cell", "object": args.object,
                         "cell": args.cell, "value": args.value})
    elif args.cmd == "set-alias":
        single_op(args, {"op": "set-alias", "object": args.object,
                         "cell": args.cell, "alias": args.alias})
    elif args.cmd == "batch":
        ops = json.loads(Path(args.ops_json).read_text())
        if not isinstance(ops, list):
            sys.exit("OPS.json must contain a JSON list of op objects")
        if not f.exists():
            sys.exit(f"no such file: {f}")
        run({"file": str(f), "ops": ops}, f, is_new=False)
    elif args.cmd == "recompute":
        if not f.exists():
            sys.exit(f"no such file: {f}")
        run({"file": str(f), "ops": [], "save": False}, f, is_new=False)
    elif args.cmd == "new":
        if f.exists():
            sys.exit(f"refusing to overwrite existing {f}")
        f.parent.mkdir(parents=True, exist_ok=True)
        run({"file": str(f), "create": True, "template": args.template}, f, is_new=True)
    elif args.cmd == "run-script":
        script = Path(args.script).resolve()
        if not script.exists():
            sys.exit(f"no such script: {script}")
        payload = {"file": str(f), "script": str(script), "force_save": args.force_save}
        if args.new:
            if f.exists():
                sys.exit(f"refusing to overwrite existing {f}")
            payload["create"] = True
            payload["template"] = args.template
            run(payload, f, is_new=True)
        else:
            if not f.exists():
                sys.exit(f"no such file: {f} (use --new to create)")
            run(payload, f, is_new=False)


if __name__ == "__main__":
    main()
