# L-bracket: 40x20x4 base plate with two 4mm mounting holes.
# Runs under fcstd_edit.py run-script: `doc` and `App` are provided.
import Part
import Sketcher

V = App.Vector
body = doc.getObject("Body") or doc.addObject("PartDesign::Body", "Body")
doc.recompute()  # ensure origin planes exist

sk = body.newObject("Sketcher::SketchObject", "BaseSketch")
sk.AttachmentSupport = (doc.getObject("XY_Plane"), [""])
sk.MapMode = "FlatFace"
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
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 1, -1, 1))
w = sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, 40.0))
sk.renameConstraint(w, "plateWidth")
h = sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, 20.0))
sk.renameConstraint(h, "plateDepth")

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
pocket.Type = 1  # through all
pocket.Reversed = True  # sketch is on XY plane at Z=0; cut upward into the pad
