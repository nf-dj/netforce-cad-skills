"""Shared infrastructure for the kicad skill scripts.

- kicad-cli discovery (env override KICAD_CLI)
- dependency self-bootstrap (venv inside the skill dir)
- backup + atomic write helpers
- netlist export/parse (authoritative connectivity + verification)
- ERC runner
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
GRID = 1.27  # mm, KiCad 50 mil connection grid


# ---------------------------------------------------------------- bootstrap

def ensure_deps() -> None:
    """Re-exec under the skill venv if kicad_tools is not importable."""
    try:
        import kicad_tools  # noqa: F401
        return
    except ImportError:
        pass
    venv = SKILL_DIR / ".venv"
    py = venv / "bin" / "python"
    if not py.exists():
        sys.stderr.write("[kicad skill] bootstrapping venv with dependencies...\n")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "-r", str(SKILL_DIR / "requirements.txt")],
            check=True,
        )
    if os.environ.get("_KICAD_SKILL_REEXEC") == "1":
        sys.exit("[kicad skill] venv bootstrap failed: kicad_tools still missing")
    os.environ["_KICAD_SKILL_REEXEC"] = "1"
    os.execv(str(py), [str(py)] + sys.argv)


# ---------------------------------------------------------------- kicad-cli

_CLI_CANDIDATES = [
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/bin/kicad-cli",
    "/usr/local/bin/kicad-cli",
]


def find_kicad_cli() -> str:
    env = os.environ.get("KICAD_CLI")
    if env:
        if not Path(env).exists():
            sys.exit(f"KICAD_CLI={env} does not exist")
        return env
    found = shutil.which("kicad-cli")
    if found:
        return found
    for c in _CLI_CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit(
        "kicad-cli not found. Install KiCad 9+ or set KICAD_CLI to the binary path."
    )


def run_cli(*args: str, ok_codes=(0,)) -> subprocess.CompletedProcess:
    cli = find_kicad_cli()
    proc = subprocess.run([cli, *args], capture_output=True, text=True)
    if proc.returncode not in ok_codes:
        raise RuntimeError(
            f"kicad-cli {' '.join(args)} failed rc={proc.returncode}: {proc.stderr.strip()[:800]}"
        )
    return proc


# ---------------------------------------------------------------- file safety

def backup(path: Path) -> Path:
    bak = path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, bak)
    return bak


def atomic_write_text(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- netlist / ERC

def export_netlist(sch: Path) -> str:
    """KiCad-exported netlist text with volatile lines removed."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.net"
        run_cli("sch", "export", "netlist", "--output", str(out), str(sch))
        return "\n".join(
            l for l in out.read_text().splitlines()
            if "(date " not in l and "(source " not in l and "(tstamps" not in l
        )


def netlist_nets(sch: Path) -> dict[str, list[str]]:
    """Authoritative connectivity: net name -> sorted ['REF.pin(name)', ...]."""
    from kicad_tools.sexp import parse_string

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.net"
        run_cli("sch", "export", "netlist", "--output", str(out), str(sch))
        doc = parse_string(out.read_text())
    nets: dict[str, list[str]] = {}
    nets_sec = doc.find_child("nets")
    if nets_sec is None:
        return nets
    for net in nets_sec.find_children("net"):
        name = net.find_child("name").get_string(0)
        members = []
        for node in net.find_children("node"):
            ref = node.find_child("ref").get_string(0)
            pin = node.find_child("pin").get_string(0)
            pinfn = node.find_child("pinfunction")
            fn = f"({pinfn.get_string(0)})" if pinfn is not None else ""
            members.append(f"{ref}.{pin}{fn}")
        nets[name] = sorted(members)
    return nets


def netlist_components(sch: Path) -> dict[str, dict]:
    """Netlist components section: ref -> {value, footprint}."""
    from kicad_tools.sexp import parse_string

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.net"
        run_cli("sch", "export", "netlist", "--output", str(out), str(sch))
        doc = parse_string(out.read_text())
    comps: dict[str, dict] = {}
    sec = doc.find_child("components")
    if sec is None:
        return comps
    for c in sec.find_children("comp"):
        ref = c.find_child("ref").get_string(0)
        val = c.find_child("value")
        fp = c.find_child("footprint")
        comps[ref] = {"value": val.get_string(0) if val is not None else "",
                      "footprint": fp.get_string(0) if fp is not None else ""}
    return comps


def run_erc(sch: Path) -> dict:
    """Run ERC, return parsed JSON report. rc 5 = violations found (still valid)."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "erc.json"
        run_cli("sch", "erc", "--format", "json", "--units", "mm", "--severity-error",
                "--severity-warning", "--output", str(out), str(sch), ok_codes=(0, 5))
        return json.loads(out.read_text())


def erc_summary(report: dict) -> dict:
    """Condense a kicad-cli ERC report to counts + findings list."""
    findings = []
    for sheet in report.get("sheets", []):
        for v in sheet.get("violations", []):
            pos = v.get("items", [{}])[0].get("pos", {})
            # kicad-cli JSON reports positions in 100mm units regardless of --units
            # (verified: violations land exactly on known pin coordinates when x100)
            findings.append({
                "severity": v.get("severity"),
                "type": v.get("type"),
                "description": v.get("description"),
                "at": [round(pos.get("x", 0) * 100, 3), round(pos.get("y", 0) * 100, 3)],
                "sheet": sheet.get("path"),
            })
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"errors": errors, "warnings": warnings, "findings": findings}


# ---------------------------------------------------------------- geometry

def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


def is_on_grid(x: float, y: float) -> bool:
    return abs(x - snap(x)) < 1e-4 and abs(y - snap(y)) < 1e-4


def fmt_pt(p) -> str:
    x, y = p
    return f"({x:g},{y:g})"
