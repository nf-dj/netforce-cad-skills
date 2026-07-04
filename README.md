# netforce-cad-skills

Claude Code skills that let agents **view, edit, and create KiCad and FreeCAD
files entirely headlessly** — no GUI required.

Intended workflow: the user looks at a rendering in a separate viewer and asks
the agent to make a change. The agent reads a *semantic text view* of the file
(exact coordinates, pin positions, nets, sketch constraints, parameters), plans
the change, and applies it with targeted edit commands that verify themselves
(ERC / recompute). The user re-checks the viewer.

## Skills

| Skill | Views | Edits | Creates | Renders |
|---|---|---|---|---|
| `kicad` | schematics (`.kicad_sch`) + boards (`.kicad_pcb`) | schematics | new projects + flat schematics | schematic/board PNG + PDF, 3D board PNG |
| `freecad` | `.FCStd` (object tree, sketches, constraints, spreadsheets) | parametric values | new documents via scripted authoring | shaded multi-view PNG + PDF |

## Install

Copy or symlink the skill directories into a project's `.claude/skills/` (or
`~/.claude/skills/` to enable them everywhere):

```sh
ln -s "$(pwd)/.claude/skills/kicad" ~/.claude/skills/kicad
ln -s "$(pwd)/.claude/skills/freecad" ~/.claude/skills/freecad
```

## Requirements

- **KiCad 9** — for `kicad-cli` (ERC, netlist verification). Override the path
  with `KICAD_CLI`. Python deps (`kicad-tools`, `sexpdata`) auto-install into a
  venv inside the skill on first run.
- **FreeCAD 1.0** — only for *editing/creating* `.FCStd` (headless `freecadcmd`;
  override with `FREECADCMD`). Viewing `.FCStd` needs nothing but Python stdlib.

## Tests

```sh
python3 -m venv .venv && .venv/bin/pip install -r .claude/skills/kicad/scripts/requirements.txt
.venv/bin/python tests/test_kicad_roundtrip.py   # backend fidelity gate
# FreeCAD fixture regeneration:
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd tests/make_freecad_fixtures.py
```
