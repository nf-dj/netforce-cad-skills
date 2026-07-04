# FreeCAD build recipes (for `fcstd_edit.py run-script`)

Scripts run inside headless FreeCAD with `doc` (open document) and
`App`/`FreeCAD` in scope. Import `Part`/`Sketcher` yourself. The harness
recomputes once at the end and saves only if clean — but call
`doc.recompute()` yourself whenever later code references shapes created
earlier (faces, origin planes).

## Pattern: parametric plate with holes (tested)

```python
import Part, Sketcher
V = App.Vector

body = doc.getObject("Body") or doc.addObject("PartDesign::Body", "Body")
doc.recompute()                     # materializes the Body's origin planes

sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
sk.AttachmentSupport = (doc.getObject("XY_Plane"), [""])
sk.MapMode = "FlatFace"

# closed rectangle: 4 lines + coincident corners + H/V + anchor + NAMED dims
pts = [(0, 0), (40, 0), (40, 20), (0, 20)]
for i in range(4):
    a, b = pts[i], pts[(i + 1) % 4]
    sk.addGeometry(Part.LineSegment(V(*a, 0), V(*b, 0)), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
sk.addConstraint(Sketcher.Constraint("Horizontal", 0))
sk.addConstraint(Sketcher.Constraint("Horizontal", 2))
sk.addConstraint(Sketcher.Constraint("Vertical", 1))
sk.addConstraint(Sketcher.Constraint("Vertical", 3))
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))   # to origin
i = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40.0))
sk.renameConstraint(i, "plateWidth")                                # NAME IT
i = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20.0))
sk.renameConstraint(i, "plateDepth")

pad = body.newObject("PartDesign::Pad", "BasePad")
pad.Profile = sk
pad.Length = 4.0
doc.recompute()

holes = body.newObject("Sketcher::SketchObject", "HoleSketch")
holes.AttachmentSupport = (doc.getObject("XY_Plane"), [""])
holes.MapMode = "FlatFace"
for cx in (8.0, 32.0):
    i = holes.addGeometry(Part.Circle(V(cx, 10, 0), V(0, 0, 1), 2.0), False)
    holes.addConstraint(Sketcher.Constraint("Radius", i, 2.0))

pocket = body.newObject("PartDesign::Pocket", "Holes")
pocket.Profile = holes
pocket.Type = 1          # through all
pocket.Reversed = True   # sketch sits at Z=0 under the pad: cut UPWARD.
                         # Forgetting this leaves the volume unchanged —
                         # the shape report will show it.
```

## Pattern: spreadsheet-driven dimensions

```python
sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("A1", "'height"); sheet.set("B1", "30")
sheet.setAlias("B1", "height")
doc.recompute()                                  # aliases exist only after recompute
pad.setExpression("Length", "Params.height")     # now `set-cell FILE Params B1 40` edits the part
```

## Pattern: primitives + booleans (Part workbench, no sketches)

```python
box = doc.addObject("Part::Box", "Housing")
box.Length, box.Width, box.Height = 60, 40, 25
hole = doc.addObject("Part::Cylinder", "CableHole")
hole.Radius, hole.Height = 4, 60
hole.Placement = App.Placement(App.Vector(30, -5, 12.5),
                               App.Rotation(App.Vector(1, 0, 0), -90))
cut = doc.addObject("Part::Cut", "Housing_cut")
cut.Base, cut.Tool = box, hole
```

## Constraint cheat sheet

`Sketcher.Constraint(type, ...)` — geometry indices are 0-based in creation
order; point codes: 1 = start, 2 = end, 3 = center; index −1 = sketch origin
point, −2/−3 = X/Y axis.

| goal | call |
|---|---|
| join two endpoints | `("Coincident", g1, 2, g2, 1)` |
| horizontal / vertical line | `("Horizontal", g)` / `("Vertical", g)` |
| anchor point to origin | `("Coincident", g, 1, -1, 1)` |
| width (x-distance between points) | `("DistanceX", g, 1, g, 2, mm)` |
| height | `("DistanceY", g, 1, g, 2, mm)` |
| circle radius / diameter | `("Radius", g, mm)` / `("Diameter", g, mm)` |
| angle between lines | `("Angle", g1, 1, g2, 1, radians)` |
| point on line | `("PointOnObject", g1, 1, g2)` |
| equal length/radius | `("Equal", g1, g2)` |
| symmetric about axis | `("Symmetric", g1, 1, g2, 2, -2)` |

Aim for "fully constrained" in the deep view; rename every driving
dimensional constraint you may want to edit later.
