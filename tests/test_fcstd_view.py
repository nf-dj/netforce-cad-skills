"""fcstd_view tests (stdlib parse + optional --deep via freecadcmd).

Run: python3 tests/test_fcstd_view.py
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills/freecad/scripts/fcstd_view.py"

FIXTURES = sorted((REPO / "fixtures/freecad").glob("*.FCStd")) + sorted(
    (REPO / "fixtures/real").glob("*.FCStd"))


def view(*args) -> str:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          check=True, capture_output=True, text=True).stdout


def main() -> int:
    for f in FIXTURES:
        m = json.loads(view(str(f), "--json"))
        assert m["objects"], f"{f.name}: no objects"
        again = json.loads(view(str(f), "--json"))
        assert m == again, f"{f.name}: non-deterministic"
        sketches = [o for o in m["objects"] if "sketch" in o]
        sheets = [o for o in m["objects"] if "spreadsheet" in o]
        print(f"OK {f.name}: {len(m['objects'])} objects, "
              f"{len(sketches)} sketches, {len(sheets)} spreadsheets")

    # param_box specifics: named constraints + expression + alias must surface
    m = json.loads(view(str(REPO / "fixtures/freecad/param_box.FCStd"), "--json"))
    sketch = next(o for o in m["objects"] if o["name"] == "Sketch")
    names = {c.get("name") for c in sketch["sketch"]["constraints"]}
    assert {"boxWidth", "boxHeight"} <= names, names
    pad = next(o for o in m["objects"] if o["name"] == "Pad")
    assert pad["expressions"] == {"Length": "Params.pad_length"}, pad.get("expressions")
    params = next(o for o in m["objects"] if o["name"] == "Params")
    assert params["spreadsheet"]["aliases"] == {"pad_length": "B1"}
    print("OK param_box semantics: named constraints, expression binding, alias")

    # deep on param_box
    m = json.loads(view(str(REPO / "fixtures/freecad/param_box.FCStd"), "--json", "--deep"))
    deep = m["deep"]
    assert not deep["errors"], deep["errors"]
    assert abs(deep["objects"]["Pad"]["volume"] - 1000.0) < 1e-3
    assert deep["objects"]["Sketch"]["fully_constrained"] is True
    print("OK --deep: clean recompute, Pad volume 1000, sketch fully constrained")

    # render smoke test (png + pdf, multi-view)
    import tempfile
    render = REPO / "skills/freecad/scripts/fcstd_render.py"
    with tempfile.TemporaryDirectory() as td:
        for out in (f"{td}/r.png", f"{td}/r.pdf"):
            r = subprocess.run([sys.executable, str(render),
                                str(REPO / "fixtures/freecad/param_box.FCStd"),
                                "-o", out, "--views", "iso,top"],
                               check=True, capture_output=True, text=True)
            rep = json.loads(r.stdout)
            assert Path(rep["rendered"]).stat().st_size > 5000
        print("OK render: png + pdf, iso+top views")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
