#!/usr/bin/env python3
"""Verification utilities for KiCad schematics.

Usage:
  kicad_verify.py roundtrip FILE      # parse→serialize→compare (safe, no writes)
  kicad_verify.py erc FILE            # ERC report (JSON)
  kicad_verify.py netlist-diff A B    # compare connectivity of two schematics
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import kicad_common as kc

kc.ensure_deps()


def nets_diff(a: dict[str, list[str]], b: dict[str, list[str]]) -> dict:
    """Structural connectivity diff between two net maps."""
    added = {n: b[n] for n in b if n not in a}
    removed = {n: a[n] for n in a if n not in b}
    changed = {n: {"before": a[n], "after": b[n]} for n in a if n in b and a[n] != b[n]}
    return {"nets_added": added, "nets_removed": removed, "nets_changed": changed,
            "identical": not (added or removed or changed)}


def cmd_roundtrip(file: Path) -> int:
    from kicad_tools import load_schematic, save_schematic

    sexp = load_schematic(file)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / file.name
        save_schematic(sexp, out)
        byte_identical = file.read_bytes() == out.read_bytes()
        load_schematic(out)  # re-parse sanity
        d = nets_diff(kc.netlist_nets(file), kc.netlist_nets(out))
    print(json.dumps({"byte_identical": byte_identical,
                      "reparses": True, "connectivity": d}, indent=1))
    return 0 if d["identical"] else 1


def cmd_erc(file: Path) -> int:
    rep = kc.erc_summary(kc.run_erc(file))
    print(json.dumps(rep, indent=1))
    return 0 if rep["errors"] == 0 else 1


def cmd_netlist_diff(a: Path, b: Path) -> int:
    d = nets_diff(kc.netlist_nets(a), kc.netlist_nets(b))
    print(json.dumps(d, indent=1))
    return 0 if d["identical"] else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("roundtrip"); r.add_argument("file", type=Path)
    e = sub.add_parser("erc"); e.add_argument("file", type=Path)
    n = sub.add_parser("netlist-diff"); n.add_argument("a", type=Path); n.add_argument("b", type=Path)
    args = ap.parse_args()
    if args.cmd == "roundtrip":
        sys.exit(cmd_roundtrip(args.file))
    if args.cmd == "erc":
        sys.exit(cmd_erc(args.file))
    sys.exit(cmd_netlist_diff(args.a, args.b))


if __name__ == "__main__":
    main()
