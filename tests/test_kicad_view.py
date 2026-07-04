"""kicad_view tests: completeness, pin-position accuracy, determinism.

Run: .venv/bin/python tests/test_kicad_view.py
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills/kicad/scripts"
PY = REPO / ".venv/bin/python"

SCH_FIXTURES = sorted((REPO / "fixtures/kicad").glob("*/*.kicad_sch")) + sorted(
    (REPO / "fixtures/real").glob("**/*.kicad_sch"))
PCB_FIXTURES = sorted((REPO / "fixtures/kicad").glob("*/*.kicad_pcb")) + sorted(
    (REPO / "fixtures/real").glob("**/*.kicad_pcb"))


def view(*args) -> str:
    return subprocess.run(
        [str(PY), str(SCRIPTS / "kicad_view.py"), *args],
        check=True, capture_output=True, text=True).stdout


def main() -> int:
    ok = True
    for f in SCH_FIXTURES:
        m = json.loads(view("sch", str(f), "--json", "--nets"))
        assert m["symbols"], f"{f.name}: no symbols"
        # every non-power pin must have coordinates; most must have a net
        pins = [p for s in m["symbols"] for p in s["pins"]]
        assert pins and all(len(p["at"]) == 2 for p in pins), f"{f.name}: missing pin coords"
        with_net = sum(1 for p in pins if p.get("net"))
        frac = with_net / len(pins)
        status = "OK" if frac > 0.8 else "LOW"
        if frac <= 0.8:
            ok = False
        # determinism
        again = json.loads(view("sch", str(f), "--json", "--nets"))
        assert m == again, f"{f.name}: non-deterministic output"
        print(f"{status}  sch {f.name}: {len(m['symbols'])} symbols, "
              f"{len(pins)} pins ({with_net} with nets), {len(m['wires'])} wires, "
              f"{len(m['nets'])} nets")
    for f in PCB_FIXTURES:
        m = json.loads(view("pcb", str(f), "--json"))
        assert m["footprints"] is not None
        assert m["board_size_mm"], f"{f.name}: no board size"
        pads = [p for fp in m["footprints"] for p in fp["pads"]]
        print(f"OK  pcb {f.name}: {len(m['footprints'])} footprints, {len(pads)} pads, "
              f"board {m['board_size_mm']}")
    # render smoke tests
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sch = SCH_FIXTURES[0]
        pcb = PCB_FIXTURES[0]
        for name, args in [
            ("sch png", ["render", str(sch), "-o", f"{td}/s.png"]),
            ("sch pdf", ["render", str(sch), "-o", f"{td}/s.pdf"]),
            ("pcb 2d png", ["render", str(pcb), "-o", f"{td}/p.png"]),
            ("pcb 3d png", ["render", str(pcb), "--3d", "-o", f"{td}/p3.png"]),
        ]:
            out = json.loads(view(*args))
            for f in out["rendered"]:
                assert Path(f).stat().st_size > 5000, f"{name}: tiny output {f}"
            print(f"OK  render {name}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
