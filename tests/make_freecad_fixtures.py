"""Generate fixtures/freecad/param_box.FCStd — a small parametric model that
exercises exactly the edit surface the freecad skill targets:
  - Sketch with NAMED driving constraints (boxWidth, boxHeight)
  - Spreadsheet with alias (pad_length) driving the Pad via expression
Run under freecadcmd:
  /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd tests/make_freecad_fixtures.py
"""
import os
import sys

import FreeCAD as App
import Part
import Sketcher

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "freecad", "param_box.FCStd")

doc = App.newDocument("param_box")

body = doc.addObject("PartDesign::Body", "Body")
sketch = body.newObject("Sketcher::SketchObject", "Sketch")
sketch.AttachmentSupport = (doc.getObject("XY_Plane"), [""])
sketch.MapMode = "FlatFace"

# rectangle 20 x 10 anchored at origin
V = App.Vector
lines = [
    (V(0, 0, 0), V(20, 0, 0)),
    (V(20, 0, 0), V(20, 10, 0)),
    (V(20, 10, 0), V(0, 10, 0)),
    (V(0, 10, 0), V(0, 0, 0)),
]
for a, b in lines:
    sketch.addGeometry(Part.LineSegment(a, b), False)
# coincident corners
for i in range(4):
    sketch.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
sketch.addConstraint(Sketcher.Constraint("Horizontal", 0))
sketch.addConstraint(Sketcher.Constraint("Horizontal", 2))
sketch.addConstraint(Sketcher.Constraint("Vertical", 1))
sketch.addConstraint(Sketcher.Constraint("Vertical", 3))
# anchor to origin
sketch.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
# named driving dimensions
c_w = sketch.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 20.0))
sketch.renameConstraint(c_w, "boxWidth")
c_h = sketch.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 10.0))
sketch.renameConstraint(c_h, "boxHeight")

pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sketch
pad.Length = 5.0

sheet = doc.addObject("Spreadsheet::Sheet", "Params")
sheet.set("A1", "pad_length")
sheet.set("B1", "5")
sheet.setAlias("B1", "pad_length")

doc.recompute()
pad.setExpression("Length", "Params.pad_length")

rc = doc.recompute()
errors = [o.Name for o in doc.Objects if "Invalid" in o.State]
if errors:
    sys.stderr.write("FIXTURE ERRORS: %s\n" % errors)
    sys.exit(1)

vol = pad.Shape.Volume
assert abs(vol - 20 * 10 * 5) < 1e-6, vol
doc.saveAs(os.path.abspath(OUT))
sys.stderr.write("WROTE %s (volume=%s)\n" % (os.path.abspath(OUT), vol))
