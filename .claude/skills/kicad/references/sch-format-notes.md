# KiCad 9 .kicad_sch anatomy (format 20250114)

Only needed for debugging — the scripts handle all of this.

Top-level sections, in order: `version`, `generator`, `generator_version`,
`uuid` (root sheet uuid), `paper`, `title_block?`, `lib_symbols`, then content
(`junction`, `no_connect`, `wire`, `label`, `global_label`,
`hierarchical_label`, `symbol`, `sheet`), then `sheet_instances`.

- **lib_symbols** — embedded copies of every symbol used, keyed by full
  `LIB:NAME` id. Sub-units are named `NAME_<unit>_<style>`; unit 0 = graphics
  common to all units. When adding a symbol, copy the definition verbatim from
  the system library (a re-serialized copy triggers `lib_symbol_mismatch` ERC
  warnings) and embed the `extends` base too if there is one.
- **symbol instance** — `(symbol (lib_id ...) (at X Y ROT) (mirror x|y)?
  (unit N) (uuid ...) (property ...)... (pin "N" (uuid ...))... (instances
  (project "NAME" (path "/<root-uuid>" (reference "R1") (unit 1)))))`.
  The `instances` block is what makes a KiCad 9 symbol *count* — a symbol
  without it doesn't netlist under the project.
- **Pin absolute position** = instance `at` + transform of the lib pin's
  `(at px py angle)`: mirror in lib coords (mirror x ⇒ py = −py, mirror y ⇒
  px = −px), rotate the instance rotation **clockwise**, then flip Y
  (sheet Y grows down, lib Y grows up): abs = (ix + px', iy − py').
- **wire** — `(wire (pts (xy x1 y1) (xy x2 y2)) (stroke ...) (uuid ...))`.
  Connectivity is purely geometric: coincident endpoints/pins connect.
- **coordinates** — mm, 1.27 mm (50 mil) connection grid.
- kicad-cli JSON ERC/DRC reports give positions in **100 mm units**
  (multiply by 100 for mm); our scripts already convert.
