---
name: kicad
description: >-
  View, edit, and create KiCad files headlessly (no GUI). Semantic text views
  of schematics (.kicad_sch) and PCBs (.kicad_pcb) with exact coordinates, pin
  positions, and net connectivity; targeted self-verifying schematic edits
  (component values/footprints/properties, add/remove components, wires,
  labels, power symbols); new projects from scratch; ERC/DRC, netlist, BOM.
  Use for any request involving KiCad, schematics, PCBs, .kicad_sch,
  .kicad_pcb, component values, nets, ERC, or "create a schematic".
---

# KiCad skill

All scripts live in `scripts/` next to this file and run with any `python3`
(dependencies auto-install into a local venv on first run). `kicad-cli` from
KiCad 9+ must be installed (`KICAD_CLI` env var overrides the path; symbol
libraries via `KICAD9_SYMBOL_DIR`).

Capabilities: **view** schematics + boards, **edit/create** schematics.
Do not edit `.kicad_pcb` files or hand-edit s-expressions — layout changes
are out of scope, and hand edits bypass every safety check.

## The loop: view → edit → verify → view

1. **View** — get the complete semantic picture, then plan edits against it:
   ```sh
   python3 scripts/kicad_view.py sch FILE            # full: symbols with per-pin absolute
                                                     # positions + nets, wires, labels, junctions
   python3 scripts/kicad_view.py sch FILE --erc      # append ERC findings (baseline before editing!)
   python3 scripts/kicad_view.py sch FILE --summary  # orientation pass for big schematics
   python3 scripts/kicad_view.py pcb FILE [--drc]    # board view (footprints, abs pad positions, nets)
   python3 scripts/kicad_view.py ... --json          # machine-readable
   ```
2. **Edit** — targeted ops (each prints a JSON report and backs up the file):
   ```sh
   python3 scripts/kicad_edit.py set-value FILE R5 10k
   python3 scripts/kicad_edit.py set-footprint FILE R5 Resistor_SMD:R_0603_1608Metric
   python3 scripts/kicad_edit.py set-property FILE R5 Tolerance 1%
   python3 scripts/kicad_edit.py add-component FILE --lib-id Device:R --ref R10 --value 10k --at 100.33,50.8
   python3 scripts/kicad_edit.py remove-component FILE R5
   python3 scripts/kicad_edit.py add-power FILE --name GND --at 100.33,67.31
   python3 scripts/kicad_edit.py add-wire FILE 100.33,54.61 100.33,59.69
   python3 scripts/kicad_edit.py add-label FILE VOUT --at 100.33,57.15 [--global|--hier]
   python3 scripts/kicad_edit.py add-junction FILE --at X,Y     # where 3+ wires meet
   python3 scripts/kicad_edit.py add-no-connect FILE --at X,Y   # silence unused-pin ERC
   python3 scripts/kicad_edit.py remove-wire FILE --uuid PREFIX | --at X,Y
   python3 scripts/kicad_edit.py remove-label FILE TEXT [--at X,Y]
   python3 scripts/kicad_edit.py annotate FILE                  # assign numbers to R?/C?/U?
   python3 scripts/kicad_edit.py new-project DIR NAME           # scaffold .kicad_pro/.kicad_sch/.kicad_pcb
   ```
3. **Read the report.** Every edit prints: what changed, the backup path, a
   connectivity diff (nets/components added/removed/changed vs. before), and
   ERC results. A wrong-looking connectivity diff means your edit didn't do
   what you intended — restore the backup (`cp FILE.bak-* FILE`) and re-plan.
   Ops that must not change connectivity (set-value etc.) auto-restore and
   exit non-zero if it changed anyway.
4. **View again** to confirm the result matches the plan.

Standalone verification: `scripts/kicad_verify.py roundtrip|erc|netlist-diff`.

## Rendering for humans ("show me the schematic/board")

```sh
python3 scripts/kicad_view.py render FILE.kicad_sch -o out.png      # schematic image
python3 scripts/kicad_view.py render FILE.kicad_sch -o out.pdf      # native vector PDF
python3 scripts/kicad_view.py render FILE.kicad_pcb -o out.png      # 2D layers, auto-fit to board
                                                                    # (--layers / --full-page / --dpi)
python3 scripts/kicad_view.py render FILE.kicad_pcb --3d -o out.png # raytraced 3D board (--side top|bottom)
```
Multi-sheet schematics produce `out-p1.png`, `out-p2.png`, ... After rendering
a PNG, Read it to display it in the conversation so the user can check the
result. Renders are for humans — plan edits from the text views, not pixels.

## Rules that make blind editing work

- **Grid discipline**: everything electrical sits on the 1.27 mm grid.
  Wire endpoints must EXACTLY equal pin positions from the view — same
  numbers, all 4 decimals. Edit commands snap and warn on off-grid input.
- Sheet coordinates are mm; **Y grows downward**.
- A wire ending in the middle of another wire needs a junction at the tee.
- Labels/power symbols connect only when placed exactly on a wire end or pin.
- Unconnected input pins fail ERC; add no-connects for deliberately unused pins.
- ERC on real projects often has pre-existing warnings — record the baseline
  (`--erc` before editing) and compare counts after; only NEW findings are yours.
- Power nets: KiCad's netlist omits single-pin nets, so a power symbol +
  one component pin won't show as a net until a second real pin joins it.
- Building from scratch: every power net needs one `add-power --name PWR_FLAG`
  on it, or ERC reports "power pin not driven".
- Multi-sheet schematics: edit the sub-sheet .kicad_sch file that actually
  contains the symbol (see the view's sheet list).

## References

- `references/edit-recipes.md` — worked examples: rewire a net, build a
  voltage divider from an empty project (the from-scratch pattern).
- `references/sch-format-notes.md` — KiCad 9 s-expression anatomy, for
  debugging when a report looks wrong.
