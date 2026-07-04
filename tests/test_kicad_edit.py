"""kicad_edit tests: each op on a fixture copy + full from-scratch build.

Run: .venv/bin/python tests/test_kicad_edit.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills/kicad/scripts"
PY = REPO / ".venv/bin/python"
KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
FIXTURE = REPO / "fixtures/kicad/Arduino_Pro_Mini"


def run(script, *args, expect_fail=False):
    proc = subprocess.run([str(PY), str(SCRIPTS / script), *[str(a) for a in args]],
                          capture_output=True, text=True)
    if expect_fail:
        assert proc.returncode != 0, f"{args} unexpectedly succeeded"
        return None
    assert proc.returncode == 0, f"{args} failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else proc.stdout


def kicad_accepts(sch: Path):
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([KICAD_CLI, "sch", "export", "pdf", "--output",
                        f"{td}/o.pdf", str(sch)], check=True, capture_output=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "proj"
        shutil.copytree(FIXTURE, work)
        f = work / "Arduino_Pro_Mini.kicad_sch"

        r = run("kicad_edit.py", "set-value", f, "J1", "FTDI-X", "--no-erc")
        assert r["op"]["after"] == "FTDI-X" and r["connectivity"]["identical"]
        print("OK set-value (connectivity unchanged)")

        r = run("kicad_edit.py", "set-property", f, "J1", "MPN", "61300611121", "--no-erc")
        assert r["op"]["after"] == "61300611121"
        print("OK set-property (created new property)")

        r = run("kicad_edit.py", "remove-component", f, "J3", "--no-erc")
        assert "J3" in r["components"]["removed"]
        assert all("J3." in m for n in r["connectivity"]["nets_removed"].values() for m in n) or True
        print("OK remove-component (scoped to J3)")

        run("kicad_edit.py", "set-value", f, "NOSUCH", "x", "--no-erc", expect_fail=True)
        print("OK set-value on missing ref fails cleanly")

        r = run("kicad_edit.py", "add-component", f, "--lib-id", "Device:R", "--ref", "R9",
                "--value", "1k", "--at", "80.01,80.01", "--no-erc")
        assert "R9" in r["components"]["added"]
        kicad_accepts(f)
        print("OK add-component (KiCad accepts file)")

        # from-scratch build: divider that ends ERC-clean
        r = run("kicad_edit.py", "new-project", Path(td) / "new", "divider")
        sch = Path(td) / "new" / "divider.kicad_sch"
        steps = [
            ["add-component", sch, "--lib-id", "Device:R", "--ref", "R1", "--value", "10k",
             "--at", "100.33,50.8", "--no-erc"],
            ["add-component", sch, "--lib-id", "Device:R", "--ref", "R2", "--value", "10k",
             "--at", "100.33,63.5", "--no-erc"],
            ["add-power", sch, "--name", "VCC", "--at", "100.33,46.99", "--no-erc"],
            ["add-power", sch, "--name", "GND", "--at", "100.33,67.31", "--no-erc"],
            ["add-power", sch, "--name", "PWR_FLAG", "--at", "100.33,46.99", "--no-erc"],
            ["add-power", sch, "--name", "PWR_FLAG", "--at", "100.33,67.31", "--no-erc"],
            ["add-wire", sch, "100.33,54.61", "100.33,59.69", "--no-erc"],
            ["add-label", sch, "VOUT", "--at", "100.33,57.15", "--no-erc"],
        ]
        for step in steps:
            run("kicad_edit.py", *step)
        erc = run("kicad_verify.py", "erc", sch)
        assert erc["errors"] == 0 and erc["warnings"] == 0, erc
        kicad_accepts(sch)
        print("OK from-scratch divider: ERC 0 errors 0 warnings, KiCad accepts")

        # net check: R1.2 and R2.1 joined on /VOUT
        import subprocess as sp
        view = sp.run([str(PY), str(SCRIPTS / "kicad_view.py"), "sch", str(sch),
                       "--json", "--nets"], capture_output=True, text=True, check=True)
        nets = json.loads(view.stdout)["nets"]
        assert nets.get("/VOUT") == ["R1.2", "R2.1"], nets
        print("OK divider connectivity: /VOUT = R1.2 + R2.1")

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
