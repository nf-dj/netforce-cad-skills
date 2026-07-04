"""Phase-0 fidelity gate: kicad-tools must round-trip KiCad 9 files without
semantic loss. Pass criteria per fixture:
  1. byte-identical output, OR
  2. re-parses cleanly AND netlist export (via kicad-cli) is identical AND
     ERC completes on the round-tripped file.
If this fails, the edit backend switches to the sexpdata surgical fallback.

Run: .venv/bin/python tests/test_kicad_roundtrip.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills/kicad/scripts"))

KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

FIXTURES = sorted((REPO / "fixtures/kicad").glob("*/*.kicad_sch")) + sorted(
    (REPO / "fixtures/real").glob("**/*.kicad_sch")
)


def netlist(sch: Path, out: Path) -> str:
    subprocess.run(
        [KICAD_CLI, "sch", "export", "netlist", "--output", str(out), str(sch)],
        check=True, capture_output=True, text=True,
    )
    # drop volatile lines (export timestamp)
    return "\n".join(
        l for l in out.read_text().splitlines() if "(date " not in l
    )


def main() -> int:
    from kicad_tools import load_schematic, save_schematic

    assert FIXTURES, "no fixtures found"
    failures = []
    for src in FIXTURES:
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            work = tdp / src.name
            shutil.copy(src, work)
            for extra in src.parent.iterdir():  # project file needed for ERC/netlist context
                if extra.suffix in (".kicad_pro",):
                    shutil.copy(extra, tdp / extra.name)

            sexp = load_schematic(work)
            out = tdp / ("rt_" + src.name)
            save_schematic(sexp, out)

            if work.read_bytes() == out.read_bytes():
                print(f"PASS (byte-identical)  {src.name}")
                continue

            # semantic equivalence path
            try:
                load_schematic(out)  # re-parse
            except Exception as e:
                failures.append((src.name, f"re-parse failed: {e}"))
                continue
            try:
                n1 = netlist(work, tdp / "a.net")
                # netlist embeds the source path; normalise
                shutil.copy(out, tdp / src.name)  # overwrite so path matches
                n2 = netlist(tdp / src.name, tdp / "b.net")
            except subprocess.CalledProcessError as e:
                failures.append((src.name, f"netlist export failed: {e.stderr[:500]}"))
                continue
            if n1 != n2:
                d1 = [l for l in n1.splitlines() if l not in n2.splitlines()]
                failures.append((src.name, f"netlist differs, e.g. {d1[:3]}"))
                continue
            erc = subprocess.run(
                [KICAD_CLI, "sch", "erc", "--format", "json", "--output",
                 str(tdp / "erc.json"), str(tdp / src.name)],
                capture_output=True, text=True,
            )
            if erc.returncode not in (0, 5):  # 5 = violations found, still parsed fine
                failures.append((src.name, f"ERC failed rc={erc.returncode}: {erc.stderr[:300]}"))
                continue
            print(f"PASS (semantically identical, not byte-identical)  {src.name}")

    if failures:
        print("\nGATE FAILED — use sexpdata fallback backend:")
        for name, why in failures:
            print(f"  {name}: {why}")
        return 1
    print("\nGATE PASSED — kicad-tools is the edit backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
