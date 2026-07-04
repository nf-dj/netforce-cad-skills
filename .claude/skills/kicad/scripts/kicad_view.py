#!/usr/bin/env python3
"""Semantic text views of KiCad files, complete enough for an agent to plan
precise edits without a GUI.

Usage:
  kicad_view.py sch FILE [--nets] [--erc] [--json] [--summary]
  kicad_view.py pcb FILE [--drc] [--json] [--summary]
  kicad_view.py render FILE [-o OUT.png|OUT.pdf] [--3d] [--side top|bottom] [--dpi N]

Schematic view includes, per symbol, the ABSOLUTE sheet position of every pin
(computed from the embedded library symbol + instance transform) and the net
each pin belongs to — these are the coordinates edit commands should target.
Coordinates are mm; sheet Y grows downward; pins sit on a 1.27 mm grid.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import kicad_common as kc

kc.ensure_deps()


# ------------------------------------------------------------------ helpers

def natkey(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s or "")]


def pin_abs(ix: float, iy: float, rot: float, mirror: str, px: float, py: float):
    """Absolute sheet position of a lib-symbol pin.

    Validated empirically against wire endpoints on multiple KiCad 9 designs
    (100% coincidence): mirror in lib coords, rotate clockwise, flip Y into
    sheet coords.
    """
    if mirror == "x":
        py = -py
    elif mirror == "y":
        px = -px
    r = int(rot or 0) % 360
    if r == 90:
        px, py = py, -px
    elif r == 180:
        px, py = -px, -py
    elif r == 270:
        px, py = -py, px
    return (round(ix + px, 4), round(iy - py, 4))


def rnd(p):
    return (round(p[0], 4), round(p[1], 4))


# ------------------------------------------------------------------ sch model

def sch_model(path: Path, want_nets: bool, want_erc: bool) -> dict:
    from kicad_tools import Schematic

    s = Schematic.load(str(path))

    pin_net: dict[str, str] = {}
    nets: dict[str, list[str]] = {}
    if want_nets:
        nets = kc.netlist_nets(path)
        for net, members in nets.items():
            for m in members:
                pin_net[m.split("(")[0]] = net

    symbols, power_symbols = [], []
    for sym in s.symbols:
        lib = s.get_lib_symbol_resolved(sym.lib_id)
        unit = sym.unit or 1
        pins = []
        if lib is not None:
            for p in lib.pins:
                if getattr(p, "unit", 0) not in (0, unit):
                    continue
                pos = pin_abs(*sym.position, sym.rotation, sym.mirror or "", *p.position)
                key = f"{sym.reference}.{p.number}"
                pins.append({
                    "pin": p.number,
                    "name": p.name if p.name not in (None, "~") else "",
                    "at": list(pos),
                    **({"net": pin_net[key]} if key in pin_net else {}),
                })
        pins.sort(key=lambda d: natkey(d["pin"]))
        props = {name: p.value for name, p in (sym.properties or {}).items()
                 if name not in ("Reference", "Value", "Footprint", "Datasheet", "Description")
                 and p.value not in ("", "~")}
        entry = {
            "ref": sym.reference,
            "value": sym.value,
            "lib_id": sym.lib_id,
            "footprint": sym.footprint or "",
            "at": list(rnd(sym.position)),
            "rotation": sym.rotation or 0,
            **({"mirror": sym.mirror} if sym.mirror else {}),
            **({"unit": unit} if unit != 1 else {}),
            **({"dnp": True} if sym.dnp else {}),
            **({"in_bom": False} if not sym.in_bom else {}),
            "uuid": (sym.uuid or "")[:8],
            **({"properties": props} if props else {}),
            "pins": pins,
        }
        (power_symbols if (sym.reference or "").startswith("#") else symbols).append(entry)

    symbols.sort(key=lambda d: natkey(d["ref"]))
    power_symbols.sort(key=lambda d: natkey(d["ref"]))

    model = {
        "file": str(path),
        "type": "kicad_sch",
        "version": s.version,
        "paper": s.paper,
        "title_block": {
            k: v for k, v in {
                "title": getattr(s.title_block, "title", ""),
                "date": getattr(s.title_block, "date", ""),
                "rev": getattr(s.title_block, "rev", ""),
                "company": getattr(s.title_block, "company", ""),
            }.items() if v
        } if s.title_block else {},
        "sheets": [
            {"name": sh.name, "file": sh.filename, "at": list(rnd(sh.position)),
             "pins": [{"name": p.name, "at": list(rnd(p.position))} for p in getattr(sh, "pins", [])]}
            for sh in s.sheets
        ],
        "symbols": symbols,
        "power_symbols": power_symbols,
        "wires": sorted(
            ({"from": list(rnd(w.start)), "to": list(rnd(w.end)), "uuid": w.uuid[:8]} for w in s.wires),
            key=lambda d: (d["from"], d["to"])),
        "junctions": sorted((list(rnd(j.position)) for j in s.junctions)),
        "no_connects": sorted((list(rnd(n.position)) for n in s.no_connects)),
        "labels": sorted(
            ({"text": l.text, "at": list(rnd(l.position)), "rotation": l.rotation or 0, "kind": kind}
             for kind, ls in (("local", s.labels), ("global", s.global_labels),
                              ("hier", s.hierarchical_labels)) for l in ls),
            key=lambda d: (d["text"], d["at"])),
    }
    if want_nets:
        model["nets"] = {k: nets[k] for k in sorted(nets, key=natkey)}
    if want_erc:
        model["erc"] = kc.erc_summary(kc.run_erc(path))
    return model


def sch_markdown(m: dict, summary: bool) -> str:
    L = []
    tb = m["title_block"]
    L.append(f"# Schematic: {Path(m['file']).name}")
    L.append(f"format {m['version']} | paper {m['paper']}"
             + (f" | title {tb.get('title')}" if tb.get("title") else ""))
    L.append("Coordinates in mm, Y grows downward. Pins/wires sit on the 1.27 mm grid.")
    if m["sheets"]:
        L.append("\n## Sub-sheets")
        for sh in m["sheets"]:
            L.append(f"- {sh['name']} ({sh['file']}) at {sh['at']}"
                     + (f", pins: {', '.join(p['name'] for p in sh['pins'])}" if sh["pins"] else ""))

    L.append(f"\n## Symbols ({len(m['symbols'])})")
    for s in m["symbols"]:
        flags = "".join(
            f" [{f}]" for f, on in (("DNP", s.get("dnp")), ("excl-BOM", s.get("in_bom") is False)) if on)
        mir = f" mirror={s['mirror']}" if s.get("mirror") else ""
        unit = f" unit={s['unit']}" if s.get("unit") else ""
        L.append(f"\n### {s['ref']} — {s['value']}  ({s['lib_id']}){flags}")
        L.append(f"at {tuple(s['at'])} rot {s['rotation']}{mir}{unit} | "
                 f"footprint: {s['footprint'] or '—'} | uuid {s['uuid']}")
        if s.get("properties"):
            L.append("props: " + ", ".join(f"{k}={v}" for k, v in s["properties"].items()))
        if not summary and s["pins"]:
            L.append("| pin | name | at (x,y) | net |")
            L.append("|---|---|---|---|")
            for p in s["pins"]:
                L.append(f"| {p['pin']} | {p['name']} | ({p['at'][0]:g},{p['at'][1]:g}) | {p.get('net', '?')} |")

    if m["power_symbols"] and not summary:
        L.append(f"\n## Power symbols ({len(m['power_symbols'])})")
        for s in m["power_symbols"]:
            pin = s["pins"][0] if s["pins"] else None
            at = f"pin at ({pin['at'][0]:g},{pin['at'][1]:g})" if pin else f"at {tuple(s['at'])}"
            L.append(f"- {s['ref']} {s['value']} {at}" + (f" net {pin.get('net')}" if pin and pin.get("net") else ""))

    if not summary:
        L.append(f"\n## Wires ({len(m['wires'])})")
        for w in m["wires"]:
            L.append(f"- ({w['from'][0]:g},{w['from'][1]:g}) → ({w['to'][0]:g},{w['to'][1]:g})  uuid {w['uuid']}")
        if m["junctions"]:
            L.append(f"\n## Junctions: " + " ".join(f"({x:g},{y:g})" for x, y in m["junctions"]))
        if m["no_connects"]:
            L.append(f"\n## No-connects: " + " ".join(f"({x:g},{y:g})" for x, y in m["no_connects"]))
        if m["labels"]:
            L.append(f"\n## Labels ({len(m['labels'])})")
            for l in m["labels"]:
                L.append(f"- [{l['kind']}] \"{l['text']}\" at ({l['at'][0]:g},{l['at'][1]:g}) rot {l['rotation']}")

    if "nets" in m:
        L.append(f"\n## Nets ({len(m['nets'])})")
        for net, members in m["nets"].items():
            L.append(f"- **{net}**: {', '.join(members)}")

    if "erc" in m:
        e = m["erc"]
        L.append(f"\n## ERC: {e['errors']} errors, {e['warnings']} warnings")
        for f in e["findings"]:
            L.append(f"- {f['severity']}: {f['type']} — {f['description']} at {f['at']}")
    return "\n".join(L)


# ------------------------------------------------------------------ pcb model

def edge_cuts_bbox(path: Path):
    """Bounding box of all Edge.Cuts geometry (handles gr_poly/line/rect/arc/circle)."""
    from kicad_tools.sexp import parse_file

    root = parse_file(str(path))
    xs, ys = [], []
    for node in root.iter_all():
        if getattr(node, "tag", None) in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_curve"):
            layer = node.find_child("layer")
            if layer is not None and layer.get_string(0) == "Edge.Cuts":
                for sub in node.iter_all():
                    if getattr(sub, "tag", None) in ("start", "end", "mid", "center", "xy"):
                        xs.append(sub.get_float(0))
                        ys.append(sub.get_float(1))
    if not xs:
        return None
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def pcb_model(path: Path, want_drc: bool) -> dict:
    from kicad_tools import PCB

    p = PCB.load(str(path))
    footprints = []
    for fp in p.footprints:
        pads = []
        for pad in fp.pads:
            abs_pos = p.get_pad_position(fp.reference, pad.number)
            pads.append({
                "pad": pad.number,
                "at": list(rnd(abs_pos)) if abs_pos else list(rnd(pad.position)),
                "size": list(rnd(pad.size)) if pad.size else None,
                "net": pad.net_name or "",
                "layers": pad.layers,
            })
        pads.sort(key=lambda d: natkey(d["pad"]))
        footprints.append({
            "ref": fp.reference,
            "footprint": fp.name,
            "value": fp.value,
            "layer": fp.layer,
            "at": list(rnd(fp.position)),
            "rotation": fp.rotation or 0,
            **({"dnp": True} if fp.dnp else {}),
            "pads": pads,
        })
    footprints.sort(key=lambda d: natkey(d["ref"]))

    nets = {}
    for code, net in sorted(p.nets.items()):
        name = net.name
        if not name:
            continue
        nets[name] = {
            "tracks": sum(1 for _ in p.segments_in_net(code)),
            "vias": sum(1 for _ in p.vias_in_net(code)),
        }

    bbox = edge_cuts_bbox(path)
    model = {
        "file": str(path),
        "type": "kicad_pcb",
        "board_bbox_mm": bbox,  # [xmin, ymin, xmax, ymax] from Edge.Cuts
        "board_size_mm": [round(bbox[2] - bbox[0], 3), round(bbox[3] - bbox[1], 3)] if bbox else None,
        "copper_layers": [getattr(l, "name", str(l)) for l in p.copper_layers],
        "counts": {"footprints": len(footprints), "tracks": p.segment_count,
                   "vias": p.via_count, "zones": p.zone_count},
        "footprints": footprints,
        "nets": nets,
    }
    if want_drc:
        model["drc"] = pcb_drc(path)
    return model


def pcb_drc(path: Path) -> dict:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "drc.json"
        kc.run_cli("pcb", "drc", "--format", "json", "--units", "mm", "--output", str(out), str(path), ok_codes=(0, 5))
        rep = json.loads(out.read_text())
    findings = []
    for v in rep.get("violations", []):
        pos = v.get("items", [{}])[0].get("pos", {})
        # kicad-cli JSON reports positions in 100mm units regardless of --units
        # (verified: violations land exactly on known pin coordinates when x100)
        findings.append({"severity": v.get("severity"), "type": v.get("type"),
                         "description": v.get("description"),
                         "at": [round(pos.get("x", 0) * 100, 3), round(pos.get("y", 0) * 100, 3)]})
    return {
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "unconnected": len(rep.get("unconnected_items", [])),
        "findings": findings,
    }


def pcb_markdown(m: dict, summary: bool) -> str:
    L = [f"# PCB: {Path(m['file']).name}"]
    if m["board_size_mm"]:
        L.append(f"board {m['board_size_mm'][0]:g} × {m['board_size_mm'][1]:g} mm"
                 f" (Edge.Cuts bbox {m['board_bbox_mm']})")
    L.append(f"copper layers: {', '.join(m['copper_layers'])}")
    c = m["counts"]
    L.append(f"{c['footprints']} footprints, {c['tracks']} tracks, {c['vias']} vias, {c['zones']} zones")
    L.append(f"\n## Footprints ({len(m['footprints'])})")
    for fp in m["footprints"]:
        dnp = " [DNP]" if fp.get("dnp") else ""
        L.append(f"\n### {fp['ref']} — {fp['value']} ({fp['footprint']}){dnp}")
        L.append(f"layer {fp['layer']} at {tuple(fp['at'])} rot {fp['rotation']:g}")
        if not summary:
            L.append("| pad | at (abs) | size | net | layers |")
            L.append("|---|---|---|---|---|")
            for pad in fp["pads"]:
                L.append(f"| {pad['pad']} | ({pad['at'][0]:g},{pad['at'][1]:g}) | "
                         f"{'×'.join(f'{v:g}' for v in pad['size']) if pad['size'] else '—'} | "
                         f"{pad['net']} | {','.join(pad['layers'] or [])} |")
    L.append(f"\n## Nets ({len(m['nets'])})")
    for net, info in m["nets"].items():
        L.append(f"- **{net}**: {info['tracks']} tracks, {info['vias']} vias")
    if "drc" in m:
        d = m["drc"]
        L.append(f"\n## DRC: {d['errors']} errors, {d['warnings']} warnings, {d['unconnected']} unconnected")
        for f in d["findings"][:50]:
            L.append(f"- {f['severity']}: {f['type']} — {f['description']} at {f['at']}")
    return "\n".join(L)


# ------------------------------------------------------------------ render

def pdf_to_pngs(pdf: Path, out_base: Path, dpi: int, fit_bbox_mm=None,
                bg: str = "white") -> list[Path]:
    """Rasterize. fit_bbox_mm=[xmin,ymin,xmax,ymax] crops to that region —
    KiCad plots 1:1, so page mm == board file mm (Y from page top).
    bg: background color name or #RRGGBB (plots don't paint their own)."""
    import pypdfium2 as pdfium
    from PIL import ImageColor

    fill = ImageColor.getrgb(bg) + (255,)
    PT = 72 / 25.4  # points per mm
    doc = pdfium.PdfDocument(str(pdf))
    outs = []
    try:
        n = len(doc)
        for i in range(n):
            page = doc[i]
            crop = (0, 0, 0, 0)
            if fit_bbox_mm:
                pw, ph = page.get_size()  # points
                x0, y0, x1, y1 = (v * PT for v in fit_bbox_mm)
                # crop = (left, bottom, right, top) amounts to cut off
                crop = (max(0, x0), max(0, ph - y1), max(0, pw - x1), max(0, y0))
            img = page.render(scale=dpi / 72, crop=crop, fill_color=fill).to_pil()
            page.close()
            out = out_base if n == 1 else out_base.with_name(
                f"{out_base.stem}-p{i + 1}{out_base.suffix}")
            img.save(out)
            outs.append(out)
    finally:
        doc.close()
    return outs


def cmd_render(args) -> None:
    import tempfile

    f: Path = args.file
    out: Path = args.out or f.with_suffix(".png")
    kind = "sch" if f.suffix == ".kicad_sch" else "pcb"
    want_pdf = out.suffix.lower() == ".pdf"

    if kind == "pcb" and args.three_d:
        if want_pdf:
            sys.exit("3D render outputs PNG/JPEG only — use a .png output path")
        kc.run_cli("pcb", "render", "--output", str(out), "--side", args.side,
                   "--background", "opaque", "--quality", "high",
                   "--width", "1600", "--height", "1200", str(f))
        print(json.dumps({"rendered": [str(out)], "mode": "3d", "side": args.side}))
        return

    layers = args.layers or "F.Cu,B.Cu,Edge.Cuts,F.SilkS,B.SilkS"

    theme = ["--theme", args.theme] if args.theme else []

    def export_pdf(dest: Path) -> None:
        if kind == "sch":
            kc.run_cli("sch", "export", "pdf", "--output", str(dest), *theme, str(f))
        else:
            kc.run_cli("pcb", "export", "pdf", "--output", str(dest),
                       "--layers", layers, "--include-border-title", *theme, str(f))

    # board bbox auto-fit (boards are tiny on a full plot sheet); --full-page disables
    fit_bbox = None
    dpi = args.dpi
    if kind == "pcb" and not args.full_page:
        bbox = edge_cuts_bbox(f)
        if bbox:
            m = 3.0  # mm margin
            fit_bbox = [bbox[0] - m, bbox[1] - m, bbox[2] + m, bbox[3] + m]
            if args.dpi == 200:  # default: pick dpi so the board spans ~1400 px
                dpi = int(min(4800, max(200, 1400 * 25.4 / (fit_bbox[2] - fit_bbox[0]))))

    if want_pdf:
        if fit_bbox:  # vector crop: shrink the page boxes to the board bbox
            import pypdfium2 as pdfium
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "out.pdf"
                export_pdf(tmp)
                PT = 72 / 25.4
                doc = pdfium.PdfDocument(str(tmp))
                for page in doc:
                    _, ph = page.get_size()
                    x0, y0, x1, y1 = (v * PT for v in fit_bbox)
                    box = (x0, ph - y1, x1, ph - y0)
                    page.set_mediabox(*box)
                    page.set_cropbox(*box)
                doc.save(str(out))
                doc.close()
        else:
            export_pdf(out)
        print(json.dumps({"rendered": [str(out)], "mode": kind, "format": "pdf",
                          **({"fit_mm": fit_bbox} if fit_bbox else {})}))
        return
    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "out.pdf"
        export_pdf(pdf)
        bg = args.bg or ("#001023" if kind == "pcb" else "white")
        outs = pdf_to_pngs(pdf, out, dpi, fit_bbox, bg)
    print(json.dumps({"rendered": [str(o) for o in outs], "mode": kind, "format": "png",
                      **({"fit_mm": fit_bbox, "dpi": dpi} if fit_bbox else {})}))


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sch", help="schematic semantic view")
    a.add_argument("file", type=Path)
    a.add_argument("--nets", action="store_true", help="include net connectivity (uses kicad-cli)")
    a.add_argument("--erc", action="store_true", help="append ERC report")
    a.add_argument("--json", action="store_true")
    a.add_argument("--summary", action="store_true", help="omit per-pin/wire detail")
    b = sub.add_parser("pcb", help="board semantic view")
    b.add_argument("file", type=Path)
    b.add_argument("--drc", action="store_true", help="append DRC report")
    b.add_argument("--json", action="store_true")
    b.add_argument("--summary", action="store_true")
    r = sub.add_parser("render", help="render schematic or board to PNG (for humans)")
    r.add_argument("file", type=Path)
    r.add_argument("-o", "--out", type=Path,
                   help="output file; .png (default) or .pdf for native vector output")
    r.add_argument("--3d", dest="three_d", action="store_true",
                   help="raytraced 3D board view (pcb only)")
    r.add_argument("--side", default="top", choices=["top", "bottom", "left", "right", "front", "back"])
    r.add_argument("--layers", help="pcb 2D: comma-separated layer list")
    r.add_argument("--dpi", type=int, default=200)
    r.add_argument("--full-page", action="store_true",
                   help="pcb 2D: keep the full plot sheet instead of auto-fitting to the board")
    r.add_argument("--theme", help="KiCad color theme name, passed to kicad-cli (e.g. nf_ai)")
    r.add_argument("--bg", default=None,
                   help="PNG background color (default: dark '#001023' for boards, "
                        "white for schematics)")
    args = ap.parse_args()

    if not args.file.exists():
        sys.exit(f"no such file: {args.file}")
    if args.cmd == "render":
        cmd_render(args)
    elif args.cmd == "sch":
        m = sch_model(args.file, want_nets=args.nets or not args.summary, want_erc=args.erc)
        print(json.dumps(m, indent=1) if args.json else sch_markdown(m, args.summary))
    else:
        m = pcb_model(args.file, want_drc=args.drc)
        print(json.dumps(m, indent=1) if args.json else pcb_markdown(m, args.summary))


if __name__ == "__main__":
    main()
