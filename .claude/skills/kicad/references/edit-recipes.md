# KiCad edit recipes

Worked examples of multi-step edits. `V` = `python3 scripts/kicad_view.py`,
`E` = `python3 scripts/kicad_edit.py`.

## Change a component value / footprint

```sh
V sch board.kicad_sch --summary          # find the refdes
E set-value board.kicad_sch R5 4.7k      # report must say connectivity identical
```

## Move a component's connection to a different net (rewire)

1. `V sch board.kicad_sch` — note the pin's absolute position, the wire uuid
   currently touching it, and the target net's label/wire positions.
2. Remove the old wire: `E remove-wire board.kicad_sch --uuid ab12cd34`
3. Draw the new connection, endpoint-to-endpoint on grid:
   `E add-wire board.kicad_sch 63.5,45.72 71.12,45.72`
4. If the new wire tees into an existing wire mid-span:
   `E add-junction board.kicad_sch --at 71.12,45.72`
5. Check the report's connectivity diff: exactly the intended net gained the
   pin, exactly the old net lost it. Anything else → restore backup.

## Voltage divider from an empty project (from-scratch pattern)

```sh
E new-project ~/proj divider
S=~/proj/divider.kicad_sch

# 1. place components on the 1.27mm grid, vertically stacked
E add-component $S --lib-id Device:R --ref R1 --value 10k --at 100.33,50.8
E add-component $S --lib-id Device:R --ref R2 --value 10k --at 100.33,63.5

# 2. read the view: it lists each pin's exact position
V sch $S      # R1: pins at (100.33,46.99) and (100.33,54.61); R2: (100.33,59.69)/(100.33,67.31)

# 3. power directly on the outer pins, PWR_FLAG on each power net (ERC needs it)
E add-power $S --name VCC --at 100.33,46.99
E add-power $S --name GND --at 100.33,67.31
E add-power $S --name PWR_FLAG --at 100.33,46.99
E add-power $S --name PWR_FLAG --at 100.33,67.31

# 4. wire the middle, label the output net (label goes ON the wire)
E add-wire $S 100.33,54.61 100.33,59.69
E add-label $S VOUT --at 100.33,57.15

# 5. verify: expect 0 errors 0 warnings, /VOUT = R1.2 + R2.1
python3 scripts/kicad_verify.py erc $S
V sch $S --nets
```

Key facts used above:
- `Device:R` is 7.62 mm tall: pins at symbol_y − 3.81 and symbol_y + 3.81.
  Two resistors stacked with pins 5.08 mm apart leave room for one wire.
- Power symbol pins are AT the symbol position (GND points down, VCC up —
  place them exactly on the pin they should drive).
- Common lib_ids: `Device:R`, `Device:C`, `Device:LED`, `Device:D`,
  `Connector_Generic:Conn_01x04`, `power:GND`, `power:VCC`, `power:+3V3`,
  `power:PWR_FLAG`. Anything in KiCad's bundled libraries works
  (`ls "$KICAD9_SYMBOL_DIR"` to browse; default is inside the KiCad app).

## Adding a decoupling capacitor next to an IC

1. View; find the IC's VCC pin position, e.g. (170.18, 77.47).
2. Place C on a free grid spot nearby: `E add-component $S --lib-id Device:C
   --ref C? --value 100nF --at 175.26,80.01` then `E annotate $S`.
3. Wire C pin 1 → VCC pin, C pin 2 → GND net (or place power:GND on it).
4. Confirm in the report: VCC/GND nets each gained one member.
