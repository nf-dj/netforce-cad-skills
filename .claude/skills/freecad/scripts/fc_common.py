"""Shared infrastructure for the freecad skill: freecadcmd discovery and the
driver<->in-process-script JSON protocol. Zero non-stdlib dependencies.

freecadcmd prints banner noise to stdout, so in-process scripts (fc_ops.py,
fc_inspect.py) emit their JSON result between sentinel lines; the driver
extracts it. Inputs are passed via a JSON file whose path goes in the
FC_SKILL_ARGS env var (freecadcmd argv passing is unreliable).
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
SENTINEL_BEGIN = "===CAD_SKILLS_JSON_BEGIN==="
SENTINEL_END = "===CAD_SKILLS_JSON_END==="

_CMD_CANDIDATES = [
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
    "/usr/bin/FreeCADCmd",
]


def find_freecadcmd() -> str:
    env = os.environ.get("FREECADCMD")
    if env:
        if not Path(env).exists():
            sys.exit(f"FREECADCMD={env} does not exist")
        return env
    found = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    if found:
        return found
    for c in _CMD_CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit("freecadcmd not found. Install FreeCAD 1.x or set FREECADCMD to the binary path.")


def run_in_freecad(script_name: str, args: dict, timeout: int = 300) -> dict:
    """Run a skill script inside freecadcmd, return its sentinel-delimited JSON."""
    script = SKILL_DIR / script_name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(args, f)
        args_file = f.name
    env = dict(os.environ, FC_SKILL_ARGS=args_file)
    try:
        proc = subprocess.run(
            [find_freecadcmd(), str(script)],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    finally:
        os.unlink(args_file)
    out = proc.stdout + "\n" + proc.stderr
    if SENTINEL_BEGIN in out and SENTINEL_END in out:
        payload = out.split(SENTINEL_BEGIN, 1)[1].split(SENTINEL_END, 1)[0]
        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            sys.exit(f"freecadcmd returned malformed JSON: {e}\n{payload[:500]}")
    sys.exit(
        f"freecadcmd did not return a result (rc={proc.returncode}).\n"
        f"stdout tail: {proc.stdout[-1000:]}\nstderr tail: {proc.stderr[-1000:]}"
    )


# --------------- helpers for scripts running INSIDE freecadcmd ---------------

def read_args() -> dict:
    with open(os.environ["FC_SKILL_ARGS"]) as f:
        return json.load(f)


def emit(result: dict) -> None:
    sys.stdout.write(f"\n{SENTINEL_BEGIN}\n{json.dumps(result)}\n{SENTINEL_END}\n")
    sys.stdout.flush()


def backup(path: Path) -> Path:
    bak = path.with_name(path.name + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, bak)
    return bak
