---
name: freecad
description: >-
  View, edit, and create FreeCAD .FCStd files headlessly (no GUI). Semantic
  text views of the full object tree, sketch geometry + constraints,
  parameters, expressions, and spreadsheets; parametric edits (sketch datums,
  feature properties, spreadsheet cells) with recompute verification; new
  parts from scratch via scripted authoring. Use for any request involving
  FreeCAD, .FCStd files, 3D/CAD models, sketches, constraints, or
  parametric parts.
---

# FreeCAD skill

Scripts live in `scripts/` and run with any `python3` — **viewing needs no
FreeCAD at all**; editing/creating runs headless FreeCAD via `freecadcmd`
(bundled with FreeCAD 1.x; `FREECADCMD` env var overrides the path).

## The loop: view → edit → read report → deep-view

1. **View** — the complete model as text; object **Name** (not Label) is the
   edit handle:
   ```sh
   python3 scripts/fcstd_view.py FILE                 # full: tree, properties, expressions,
                                                      # sketch geometry+constraints, spreadsheets
   python3 scripts/fcstd_view.py FILE --summary       # orientation pass for big models
   python3 scripts/fcstd_view.py FILE --object Sketch # one object in full
   python3 scripts/fcstd_view.py FILE --deep          # + recompute state, volumes, bboxes,
                                                      # sketch solver status (runs freecadcmd)
   python3 scripts/fcstd_view.py FILE --json          # machine-readable
   ```
2. **Edit** — parametric ops. Values take units ("25 mm", "30 deg"; bare
   numbers = mm/deg). Constraints by name or index from the view:
   ```sh
   python3 scripts/fcstd_edit.py set-datum FILE Sketch boxWidth "25 mm"
   python3 scripts/fcstd_edit.py set-property FILE Pad Length "15 mm"
   python3 scripts/fcstd_edit.py set-expression FILE Pad Length "Params.pad_length"
   python3 scripts/fcstd_edit.py set-cell FILE Params B2 42
   python3 scripts/fcstd_edit.py set-alias FILE Params B2 wall_thickness
   python3 scripts/fcstd_edit.py batch FILE ops.json    # several ops, ONE recompute
   python3 scripts/fcstd_edit.py recompute FILE         # verify-only, never saves
   ```
3. **Read the report**: per-op status, per-object recompute errors, and
   before/after volume + bounding box for every solid. The file is saved
   ONLY when the recompute is clean — on errors it is left untouched (a
   `.bak-*` backup also exists next to it). A solid collapsing to zero
   volume is reported as a warning: restore the backup if unintended.
4. **Deep-view** to confirm: volumes/bboxes changed as intended, sketches
   still fully constrained, no Invalid objects.

## Rendering for humans ("show me the body")

```sh
python3 scripts/fcstd_render.py FILE -o out.png                       # iso view of the Bodies
python3 scripts/fcstd_render.py FILE -o out.pdf --views iso,top,front,right   # tiled views, vector PDF
python3 scripts/fcstd_render.py FILE --object Body002 -o out.png      # specific object(s)
```
Shaded views with mm axes, via FreeCAD's bundled matplotlib (headless).
After rendering a PNG, Read it to display it in the conversation so the user
can check the result. Renders are for humans — plan edits from the text
views and the recompute report, not pixels.

## Creating parts from scratch

```sh
python3 scripts/fcstd_edit.py new FILE                        # empty doc (or --template partdesign)
python3 scripts/fcstd_edit.py run-script FILE build.py --new --template partdesign
```

`run-script` executes your Python inside headless FreeCAD with `doc` (the
open document) and `App`/`FreeCAD` in scope — write normal FreeCAD API code
(sketches + constraints, PartDesign Pad/Pocket, Part primitives + booleans).
The same safety harness applies: one recompute, save only if clean, full
shape report. Start from `references/build-recipes.md` — it has working
patterns (parametric sketch with NAMED constraints, spreadsheet-driven
dimensions, hole patterns) and the traps (pocket direction, attachment,
recompute-before-referencing-faces).

Design for editability: give driving constraints NAMES
(`sketch.renameConstraint(i, "width")`) and put key dimensions in a
spreadsheet — future edits become one-line `set-datum`/`set-cell` calls.

## Rules

- Lengths mm, angles degrees. `set-datum` on an Angle constraint takes "deg".
- The recompute report is the ground truth — never assume an edit worked
  because the command exited 0 alone; check volumes/bbox moved as expected.
- Editing a property that an expression drives is futile — the expression
  overwrites it on recompute. Clear it first: `set-expression FILE Obj Prop ""`.
- FreeCAD accepts geometrically degenerate-but-solvable values (e.g. a
  width of 0). Watch the zero-volume warning and the bbox.
- `references/freecad-api-notes.md` — setDatum/solver semantics, expression
  syntax, property types, common exceptions.
