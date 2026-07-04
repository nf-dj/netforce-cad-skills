"""fcstd_edit tests: parametric ops, error paths, from-scratch run-script build.

Run: python3 tests/test_fcstd_edit.py   (needs freecadcmd)
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills/freecad/scripts"
FIXTURE = REPO / "fixtures/freecad/param_box.FCStd"
BRACKET = REPO / "tests/build_bracket.py"


def run(*args, expect_fail=False):
    proc = subprocess.run([sys.executable, str(SCRIPTS / "fcstd_edit.py"),
                           *[str(a) for a in args]], capture_output=True, text=True)
    if expect_fail:
        assert proc.returncode != 0, f"{args} unexpectedly succeeded:\n{proc.stdout}"
        return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    assert proc.returncode == 0, f"{args} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "param_box.FCStd"
        shutil.copy(FIXTURE, f)

        r = run("set-datum", f, "Sketch", "boxWidth", "30 mm")
        assert r["saved"] and not r["errors"]
        assert abs(r["shapes"]["Pad"]["volume"] - 1500.0) < 1e-3
        print("OK set-datum by name: 20->30mm, volume 1000->1500")

        r = run("set-datum", f, "Sketch", "10", "15 mm")  # boxHeight by index
        assert abs(r["shapes"]["Pad"]["volume"] - 2250.0) < 1e-3
        print("OK set-datum by index")

        r = run("set-cell", f, "Params", "B1", "8")
        assert abs(r["shapes"]["Pad"]["volume"] - 3600.0) < 1e-3
        print("OK set-cell drives Pad via expression: volume 3600")

        r = run("set-property", f, "Pad", "Reversed", "0")
        assert r["saved"]
        print("OK set-property")

        r = run("set-datum", f, "Sketch", "nosuch", "5 mm", expect_fail=True)
        assert any("no constraint named" in e for e in r["errors"])
        assert not r["saved"]
        print("OK bad constraint name: clear error, file not saved")

        # batch: two ops, one recompute
        ops = Path(td) / "ops.json"
        ops.write_text(json.dumps([
            {"op": "set-datum", "object": "Sketch", "constraint": "boxWidth", "value": "10 mm"},
            {"op": "set-datum", "object": "Sketch", "constraint": "boxHeight", "value": "10 mm"},
        ]))
        r = run("batch", f, ops)
        assert abs(r["shapes"]["Pad"]["volume"] - 800.0) < 1e-3  # 10*10*8
        print("OK batch: 10x10x8 -> volume 800")

        # from-scratch build via run-script
        b = Path(td) / "bracket.FCStd"
        r = run("run-script", b, BRACKET, "--new", "--template", "partdesign")
        assert r["saved"] and not r["errors"]
        assert abs(r["shapes"]["Body"]["volume"] - 3099.469) < 0.01
        print("OK run-script bracket: plate with holes, volume 3099.47")

        # parametric edit of the scripted part
        r = run("set-datum", b, "BaseSketch", "plateWidth", "60 mm")
        assert abs(r["shapes"]["Body"]["volume"] - 4699.469) < 0.01
        print("OK created part is parametrically editable")

        # broken script: file must not be created
        bad = Path(td) / "bad.py"
        bad.write_text("raise RuntimeError('boom')")
        b2 = Path(td) / "never.FCStd"
        r = run("run-script", b2, bad, "--new", expect_fail=True)
        assert not b2.exists()
        print("OK broken script: error reported, no file written")

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
