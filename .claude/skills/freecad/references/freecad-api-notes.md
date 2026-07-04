# FreeCAD headless API notes

## setDatum / constraints

- `sketch.setDatum(indexOrName, App.Units.Quantity("25 mm"))` — raises
  `ValueError: Datum ... for the constraint with index N is invalid` when the
  solver rejects the value (conflicting constraints). Our harness catches
  this, reports it, and does not save.
- Only *driving* dimensional constraints (Distance/DistanceX/DistanceY/
  Angle/Radius/Diameter) have datums. Angle datums are radians in the API;
  our `set-datum` accepts "30 deg" and converts via Quantity.
- Constraint names: `sketch.renameConstraint(i, "name")`;
  `[c.Name for c in sketch.Constraints]` lists them (empty string = unnamed).
- Solver state: `sketch.solve()` → 0 = success; `sketch.FullyConstrained`.

## Spreadsheets

- `sheet.set("B1", "5")`, `sheet.get("B1")`, `sheet.setAlias("B1", "name")`.
  Text cells need a leading `'`; formulas start with `=`.
- Aliases become usable in expressions only after `doc.recompute()`.

## Expressions

- `obj.setExpression("Length", "Params.pad_length")` binds; `setExpression
  ("Length", None)` clears. Bound properties show in the view as
  `expression: Pad.Length = ...` — direct writes to them are overwritten on
  recompute.
- Expression syntax: `<<ObjectLabel>>.Property`, `Spreadsheet.alias`,
  arithmetic, `sin()/cos()/sqrt()`, units (`10 mm * 2`).

## Recompute & errors

- `doc.recompute()` returns the number of recomputed objects. Per-object
  `obj.State` contains `'Touched'`/`'Invalid'` — Invalid after recompute
  means that feature failed (our harness reports and refuses to save).
- Typical failure texts: "Pad: Result has multiple solids", "Sketch with
  conflicting constraints", "Recompute failed".
- FreeCAD happily produces zero-volume solids from degenerate-but-solvable
  sketches — check volumes, not just error state.

## Document structure (what the view shows)

- `.FCStd` is a zip; `Document.xml` holds every object with type, properties,
  dependency list, sketch geometry/constraints, and spreadsheet cells; `.brp`
  files are the BREP shape caches. The view parses the XML directly — that is
  why viewing needs no FreeCAD.
- Object **Name** is immutable and unique (the edit handle); **Label** is the
  user-visible display name and can collide.
- PartDesign: a `Body` groups Origin + sketches + features; `Body.Tip` is the
  final feature; each feature's `Profile` links its sketch. `BaseFeature`
  chains features: each one consumes the previous solid.
