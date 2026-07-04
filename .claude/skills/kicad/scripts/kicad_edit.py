#!/usr/bin/env python3
"""Targeted, self-verifying edits for KiCad schematics (.kicad_sch).

Usage:
  kicad_edit.py set-value FILE REF VALUE
  kicad_edit.py set-footprint FILE REF FOOTPRINT
  kicad_edit.py set-property FILE REF NAME VALUE
  kicad_edit.py remove-component FILE REF
  kicad_edit.py add-component FILE --lib-id Device:R --ref R10 --value 10k --at X,Y
                              [--rotation 0] [--mirror x|y] [--footprint FP]
  kicad_edit.py add-power FILE --name GND --at X,Y [--rotation 0]
  kicad_edit.py add-wire FILE X1,Y1 X2,Y2
  kicad_edit.py remove-wire FILE (--uuid PREFIX | --at X,Y)
  kicad_edit.py add-label FILE TEXT --at X,Y [--rotation 0] [--global | --hier]
  kicad_edit.py remove-label FILE TEXT [--at X,Y]
  kicad_edit.py add-junction FILE X,Y
  kicad_edit.py add-no-connect FILE X,Y
  kicad_edit.py annotate FILE
  kicad_edit.py new-project DIR NAME

Every edit: netlist snapshot -> backup -> mutate -> serialize to tmp ->
re-parse sanity check -> atomic replace -> netlist diff -> ERC. If the
connectivity diff violates the op's expectation the backup is auto-restored
and the command exits non-zero. Report is JSON on stdout.

Coordinates are mm (sheet Y grows downward) and are snapped to the 1.27 mm
connection grid — take pin positions from `kicad_view.py sch`.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import uuid as uuid_mod
from pathlib import Path

import kicad_common as kc

kc.ensure_deps()


# ---------------------------------------------------------------- sexp helpers

def find_symbol_node(root, ref: str):
    for node in root.find_children("symbol"):
        for prop in node.find_children("property"):
            if prop.get_string(0) == "Reference" and prop.get_string(1) == ref:
                return node
    return None


def get_prop_node(sym_node, name: str):
    for prop in sym_node.find_children("property"):
        if prop.get_string(0) == name:
            return prop
    return None


def set_symbol_property(s, ref: str, name: str, value: str) -> dict:
    from kicad_tools.sexp import parse_string

    node = find_symbol_node(s.sexp, ref)
    if node is None:
        sys.exit(f"no symbol with reference {ref!r} (check kicad_view.py sch output)")
    prop = get_prop_node(node, name)
    if prop is not None:
        old = prop.get_string(1)
        prop.set_atom(1, value)
        return {"ref": ref, "property": name, "before": old, "after": value}
    at = node.find_child("at")
    x, y = (at.get_float(0), at.get_float(1)) if at is not None else (0, 0)
    snippet = parse_string(
        f'(property "{name}" "{value}" (at {x} {y} 0) '
        f'(effects (font (size 1.27 1.27)) (hide yes)))'
    )
    node.insert_after("property", snippet)  # after the last existing property
    return {"ref": ref, "property": name, "before": None, "after": value}


def parse_xy(text: str) -> tuple[float, float]:
    try:
        x, y = (float(v) for v in text.replace(" ", "").split(","))
    except ValueError:
        sys.exit(f"expected coordinates as X,Y (mm), got {text!r}")
    sx, sy = kc.snap(x), kc.snap(y)
    if (sx, sy) != (round(x, 4), round(y, 4)):
        sys.stderr.write(f"note: snapped ({x},{y}) to 1.27mm grid -> ({sx},{sy})\n")
    return sx, sy


# ---------------------------------------------------------------- symbol libs

def symbols_dir() -> Path:
    import os
    env = os.environ.get("KICAD9_SYMBOL_DIR") or os.environ.get("KICAD_SYMBOL_DIR")
    if env:
        return Path(env)
    cli = Path(kc.find_kicad_cli())
    for cand in (cli.parent.parent / "SharedSupport" / "symbols",
                 Path("/usr/share/kicad/symbols")):
        if cand.is_dir():
            return cand
    sys.exit("KiCad symbol libraries not found; set KICAD9_SYMBOL_DIR")


def embed_lib_symbol_raw(s, lib_id: str) -> None:
    """Copy the symbol definition verbatim from the system library into the
    schematic's lib_symbols (raw s-expr copy — avoids lib_symbol_mismatch ERC
    warnings that a re-serialized copy would trigger). No-op if present."""
    from kicad_tools.sexp import parse_file, parse_string

    if s.get_lib_symbol(lib_id) is not None:
        return
    if ":" not in lib_id:
        sys.exit(f"lib_id must be LIBRARY:NAME, got {lib_id!r}")
    lib_name, sym_name = lib_id.split(":", 1)
    lib_file = symbols_dir() / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        sys.exit(f"symbol library not found: {lib_file}")
    root = parse_file(str(lib_file))
    target = None
    for sym in root.find_children("symbol"):
        if sym.get_string(0) == sym_name:
            target = sym
            break
    if target is None:
        sys.exit(f"symbol {sym_name!r} not found in {lib_file.name}")
    ext = target.find_child("extends")
    if ext is not None:
        # derived symbol: embed its base too (KiCad requires both)
        embed_lib_symbol_raw(s, f"{lib_name}:{ext.get_string(0)}")
    target.set_atom(0, lib_id)  # embedded entries use the full LIB:NAME id
    lib_syms = s.sexp.find_child("lib_symbols")
    if lib_syms is None:
        lib_syms = parse_string("(lib_symbols)")
        anchor = "paper" if s.sexp.find_child("paper") is not None else "uuid"
        s.sexp.insert_after(anchor, lib_syms)
    lib_syms.append(target)
    if hasattr(s, "invalidate_cache"):
        s.invalidate_cache()


# ---------------------------------------------------------------- edit runner

def run_edit(file: Path, mutate, expect: str = "report", no_erc: bool = False) -> None:
    """expect: 'identical' | 'touching:REF' | 'report'"""
    from kicad_tools import Schematic, load_schematic, save_schematic

    sys.path.insert(0, str(Path(__file__).parent))
    from kicad_verify import nets_diff

    if not file.exists():
        sys.exit(f"no such file: {file}")
    s = Schematic.load(str(file))
    pre_nets = kc.netlist_nets(file)
    pre_comps = kc.netlist_components(file)
    detail = mutate(s)

    bak = kc.backup(file)
    fd_tmp = tempfile.NamedTemporaryFile(
        dir=str(file.parent), prefix=".tmp-", suffix=file.suffix, delete=False)
    tmp = Path(fd_tmp.name)
    fd_tmp.close()
    try:
        save_schematic(s.sexp, tmp)
        load_schematic(tmp)  # corruption guard: must re-parse
    except Exception as e:
        tmp.unlink(missing_ok=True)
        sys.exit(f"edit aborted, file untouched (serialize/re-parse failed): {e}")
    tmp.replace(file)

    report = {"op": detail, "backup": str(bak)}
    ok = True
    try:
        post_nets = kc.netlist_nets(file)
        post_comps = kc.netlist_components(file)
        diff = nets_diff(pre_nets, post_nets)
        report["connectivity"] = diff
        report["components"] = {
            "added": {r: post_comps[r] for r in post_comps if r not in pre_comps},
            "removed": {r: pre_comps[r] for r in pre_comps if r not in post_comps},
            "changed": {r: {"before": pre_comps[r], "after": post_comps[r]}
                        for r in pre_comps if r in post_comps and pre_comps[r] != post_comps[r]},
        }
        if expect == "identical" and not diff["identical"]:
            ok = False
            report["error"] = "connectivity changed unexpectedly; restored backup"
        elif expect.startswith("touching:"):
            ref_name = expect.split(":", 1)[1]
            ref = ref_name + "."
            stray = [n for n, members in
                     list(diff["nets_added"].items()) + list(diff["nets_removed"].items())
                     if not any(m.startswith(ref) for m in members)]
            stray += [n for n, ch in diff["nets_changed"].items()
                      if not any(m.startswith(ref) for m in ch["before"] + ch["after"])]
            c = report["components"]
            stray += [r for r in list(c["added"]) + list(c["removed"]) + list(c["changed"])
                      if r != ref_name]
            if stray:
                ok = False
                report["error"] = f"changes not involving {ref_name}: {stray}; restored backup"
    except Exception as e:
        ok = False
        report["error"] = f"post-edit netlist export failed ({e}); restored backup"

    if not ok:
        shutil.copy2(bak, file)
        print(json.dumps(report, indent=1))
        sys.exit(1)

    if not no_erc:
        try:
            report["erc"] = kc.erc_summary(kc.run_erc(file))
        except Exception as e:
            report["erc"] = {"error": str(e)}
    print(json.dumps(report, indent=1))


# ---------------------------------------------------------------- ops

def op_set_value(args):
    run_edit(args.file, lambda s: set_symbol_property(s, args.ref, "Value", args.value),
             expect="identical", no_erc=args.no_erc)


def op_set_footprint(args):
    run_edit(args.file, lambda s: set_symbol_property(s, args.ref, "Footprint", args.footprint),
             expect="identical", no_erc=args.no_erc)


def op_set_property(args):
    run_edit(args.file, lambda s: set_symbol_property(s, args.ref, args.name, args.value),
             expect="identical", no_erc=args.no_erc)


def op_remove_component(args):
    def mutate(s):
        node = find_symbol_node(s.sexp, args.ref)
        if node is None:
            sys.exit(f"no symbol with reference {args.ref!r}")
        s.sexp.remove(node)
        return {"action": "remove-component", "ref": args.ref}
    run_edit(args.file, mutate, expect=f"touching:{args.ref}", no_erc=args.no_erc)


def op_add_component(args):
    at = parse_xy(args.at)

    def mutate(s):
        if s.get_symbol(args.ref) is not None:
            sys.exit(f"reference {args.ref!r} already exists; pick a free refdes")
        embed_lib_symbol_raw(s, args.lib_id)
        s.add_symbol(args.lib_id, args.ref, args.value, args.footprint or "",
                     at, rotation=args.rotation, mirror=args.mirror or "")
        return {"action": "add-component", "ref": args.ref, "lib_id": args.lib_id,
                "at": list(at), "rotation": args.rotation}
    run_edit(args.file, mutate, expect=f"touching:{args.ref}", no_erc=args.no_erc)


def op_add_power(args):
    at = parse_xy(args.at)
    name = args.name.split(":", 1)[-1]

    def mutate(s):
        embed_lib_symbol_raw(s, f"power:{name}")
        inst = s.add_power(name, at, rotation=args.rotation)
        return {"action": "add-power", "name": name, "ref": inst.reference, "at": list(at)}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_add_wire(args):
    p1, p2 = parse_xy(args.p1), parse_xy(args.p2)

    def mutate(s):
        w = s.add_wire(p1, p2)
        return {"action": "add-wire", "from": list(p1), "to": list(p2), "uuid": w.uuid[:8]}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_remove_wire(args):
    def mutate(s):
        removed = []
        for node in list(s.sexp.find_children("wire")):
            u = node.find_child("uuid")
            uid = u.get_string(0) if u is not None else ""
            pts = node.find_child("pts")
            ends = [(p.get_float(0), p.get_float(1)) for p in pts.find_children("xy")] if pts is not None else []
            hit = False
            if args.uuid and uid.startswith(args.uuid):
                hit = True
            if args.at:
                at = parse_xy(args.at)
                hit = any(abs(e[0] - at[0]) < 0.01 and abs(e[1] - at[1]) < 0.01 for e in ends)
            if hit:
                s.sexp.remove(node)
                removed.append({"uuid": uid[:8], "ends": ends})
        if not removed:
            sys.exit("no wire matched (check uuids/endpoints in kicad_view.py sch output)")
        return {"action": "remove-wire", "removed": removed}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_add_label(args):
    at = parse_xy(args.at)

    def mutate(s):
        if args.glob:
            s.add_global_label(args.text, at, rotation=args.rotation)
            kind = "global"
        elif args.hier:
            s.add_hierarchical_label(args.text, at, rotation=args.rotation)
            kind = "hier"
        else:
            s.add_label(args.text, at, rotation=args.rotation)
            kind = "local"
        return {"action": "add-label", "kind": kind, "text": args.text, "at": list(at)}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_remove_label(args):
    def mutate(s):
        removed = []
        at = parse_xy(args.at) if args.at else None
        for tag in ("label", "global_label", "hierarchical_label"):
            for node in list(s.sexp.find_children(tag)):
                if node.get_string(0) != args.text:
                    continue
                a = node.find_child("at")
                pos = (a.get_float(0), a.get_float(1)) if a is not None else None
                if at and pos and (abs(pos[0] - at[0]) > 0.01 or abs(pos[1] - at[1]) > 0.01):
                    continue
                s.sexp.remove(node)
                removed.append({"kind": tag, "at": list(pos) if pos else None})
        if not removed:
            sys.exit(f"no label {args.text!r} matched")
        return {"action": "remove-label", "text": args.text, "removed": removed}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_add_junction(args):
    at = parse_xy(args.at)

    def mutate(s):
        s.add_junction(at)
        return {"action": "add-junction", "at": list(at)}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_add_no_connect(args):
    from kicad_tools.sexp import parse_string
    at = parse_xy(args.at)

    def mutate(s):
        s.sexp.append(parse_string(
            f'(no_connect (at {at[0]:g} {at[1]:g}) (uuid "{uuid_mod.uuid4()}"))'))
        return {"action": "add-no-connect", "at": list(at)}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


def op_annotate(args):
    import re

    def mutate(s):
        used: dict[str, set[int]] = {}
        pending = []
        for node in s.sexp.find_children("symbol"):
            prop = get_prop_node(node, "Reference")
            if prop is None:
                continue
            ref = prop.get_string(1)
            m = re.fullmatch(r"([A-Za-z#]+)(\d+|\?)", ref or "")
            if not m:
                continue
            prefix, num = m.groups()
            if num == "?":
                pending.append((node, prop, prefix))
            else:
                used.setdefault(prefix, set()).add(int(num))
        assigned = {}
        for node, prop, prefix in pending:
            n = 1
            taken = used.setdefault(prefix, set())
            while n in taken:
                n += 1
            taken.add(n)
            new_ref = f"{prefix}{n}"
            prop.set_atom(1, new_ref)
            # also update instances block references
            for inst_ref in node.iter_all():
                if getattr(inst_ref, "tag", None) == "reference" and inst_ref.get_string(0) == f"{prefix}?":
                    inst_ref.set_atom(0, new_ref)
            assigned[f"{prefix}?"] = assigned.get(f"{prefix}?", []) + [new_ref]
        if not pending:
            return {"action": "annotate", "assigned": {}, "note": "nothing to annotate"}
        return {"action": "annotate", "assigned": assigned}
    run_edit(args.file, mutate, expect="report", no_erc=args.no_erc)


# ---------------------------------------------------------------- new-project

SCH_TEMPLATE = """(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "9.0")
\t(uuid "{uuid}")
\t(paper "A4")
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
"""

PCB_TEMPLATE = """(kicad_pcb
\t(version 20241229)
\t(generator "pcbnew")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(allow_soldermask_bridges_in_footprints no)
\t\t(tenting front back)
\t)
\t(net 0 "")
\t(embedded_fonts no)
)
"""

PRO_TEMPLATE = {
    "board": {"design_settings": {"defaults": {}}, "layer_presets": [], "viewports": []},
    "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
    "meta": {"filename": "", "version": 3},
    "net_settings": {"classes": [{
        "name": "Default", "priority": 2147483647,
        "clearance": 0.2, "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3,
        "microvia_diameter": 0.3, "microvia_drill": 0.1,
        "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
        "wire_width": 6, "bus_width": 12, "line_style": 0,
        "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
    }], "meta": {"version": 4}},
    "pcbnew": {"last_paths": {}, "page_layout_descr_file": ""},
    "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
    "sheets": [],
    "text_variables": {},
}


def op_new_project(args):
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    sch = d / f"{args.name}.kicad_sch"
    pcb = d / f"{args.name}.kicad_pcb"
    pro = d / f"{args.name}.kicad_pro"
    for f in (sch, pcb, pro):
        if f.exists():
            sys.exit(f"refusing to overwrite existing {f}")
    root_uuid = str(uuid_mod.uuid4())
    sch.write_text(SCH_TEMPLATE.format(uuid=root_uuid))
    pcb.write_text(PCB_TEMPLATE)
    import copy
    pro_data = copy.deepcopy(PRO_TEMPLATE)
    pro_data["meta"]["filename"] = pro.name
    pro_data["sheets"] = [[root_uuid, "Root"]]
    pro.write_text(json.dumps(pro_data, indent=2))
    # validate what we produced
    kc.run_cli("sch", "erc", "--output", str(d / ".erc-check.rpt"), str(sch), ok_codes=(0, 5))
    (d / ".erc-check.rpt").unlink(missing_ok=True)
    print(json.dumps({"op": {"action": "new-project", "dir": str(d), "name": args.name},
                      "files": [str(sch), str(pcb), str(pro)],
                      "validated": "kicad-cli accepts the schematic"}, indent=1))


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *names):
        p.add_argument("file", type=Path)
        for n in names:
            p.add_argument(n)
        p.add_argument("--no-erc", action="store_true")
        return p

    p = common(sub.add_parser("set-value"), "ref", "value"); p.set_defaults(fn=op_set_value)
    p = common(sub.add_parser("set-footprint"), "ref", "footprint"); p.set_defaults(fn=op_set_footprint)
    p = common(sub.add_parser("set-property"), "ref", "name", "value"); p.set_defaults(fn=op_set_property)
    p = common(sub.add_parser("remove-component"), "ref"); p.set_defaults(fn=op_remove_component)

    p = common(sub.add_parser("add-component"))
    p.add_argument("--lib-id", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--at", required=True, metavar="X,Y")
    p.add_argument("--rotation", type=float, default=0)
    p.add_argument("--mirror", choices=["x", "y"])
    p.add_argument("--footprint", default="")
    p.set_defaults(fn=op_add_component)

    p = common(sub.add_parser("add-power"))
    p.add_argument("--name", required=True, help="GND, VCC, +3V3, ... (or power:GND)")
    p.add_argument("--at", required=True, metavar="X,Y")
    p.add_argument("--rotation", type=float, default=0)
    p.set_defaults(fn=op_add_power)

    p = common(sub.add_parser("add-wire"), "p1", "p2"); p.set_defaults(fn=op_add_wire)

    p = common(sub.add_parser("remove-wire"))
    p.add_argument("--uuid"); p.add_argument("--at", metavar="X,Y")
    p.set_defaults(fn=op_remove_wire)

    p = common(sub.add_parser("add-label"), "text")
    p.add_argument("--at", required=True, metavar="X,Y")
    p.add_argument("--rotation", type=float, default=0)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--global", dest="glob", action="store_true")
    g.add_argument("--hier", action="store_true")
    p.set_defaults(fn=op_add_label)

    p = common(sub.add_parser("remove-label"), "text")
    p.add_argument("--at", metavar="X,Y")
    p.set_defaults(fn=op_remove_label)

    p = common(sub.add_parser("add-junction"))
    p.add_argument("--at", required=True, metavar="X,Y"); p.set_defaults(fn=op_add_junction)
    p = common(sub.add_parser("add-no-connect"))
    p.add_argument("--at", required=True, metavar="X,Y"); p.set_defaults(fn=op_add_no_connect)

    p = common(sub.add_parser("annotate")); p.set_defaults(fn=op_annotate)

    p = sub.add_parser("new-project")
    p.add_argument("dir"); p.add_argument("name"); p.set_defaults(fn=op_new_project)

    args = ap.parse_args()
    if args.cmd == "remove-wire" and not (args.uuid or args.at):
        ap.error("remove-wire needs --uuid or --at")
    args.fn(args)


if __name__ == "__main__":
    main()
