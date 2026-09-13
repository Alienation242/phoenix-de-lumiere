# Where this stands

Phoenix de Lumière, SW wall. One of twelve mapped surfaces in a 62 × 38 m hall.
This document is the state of the work: what exists, what has been measured,
what is verified, and what is still an open decision.

Everything with a number in it was measured on rendered pixels or on the
supplied files. Nothing here is estimated unless it says so.

---

## At a glance

| | |
|---|---|
| **status** | complete and verified end to end. Nothing is blocking delivery |
| **to deliver** | double-click `EXPORT.cmd`, pick `Proof` to check, then `Deliver` |
| **output** | two projector plates, 7200 × 2552 and 3588 × 2552, ProRes 422 HQ |
| **frames** | 3270 – 7529 of the ten-minute loop (4260 frames, 142 s at 30 fps) |
| **the piece** | 2:00 – 4:00, with 1:50 – 2:00 and 4:00 – 4:10 as hand-off |
| **render time** | about 2 hours at full resolution, ~51 GB |
| **open decisions** | which of the two mask sets to ship; whether the producer wants two plates or one stitched file; the high-quality noise has not arrived yet |

---

## 1. The deliverable

### Geometry

```
canvas    9788 x 2552
SPSW1     7200 x 2552     canvas x    0 .. 7200
SPSW2     3588 x 2552     canvas x 6200 .. 9788
overlap   1000 px         canvas x 6200 .. 7200
```

The two plates **overlap by 1000 px and both carry full brightness through it.**
That is deliberate and matches the noise plates that were supplied: the soft
edge is applied downstream by the projector blend. Baking a ramp into these
files would double-darken the seam.

Both plates are sliced from **the same frame in GPU memory**, so the overlap is
identical by construction rather than by process. `verify_plates.py` measures it
afterwards and the export refuses to report success if it fails.

Only **1, 2 and 4** are valid scale divisors: gcd(9788, 2552) = 4, and any other
divisor puts the render on a fractional pixel grid where the dither crawls.

### Timing

```
frame 3270   1:49.00     render starts (handles)
frame 3300   1:50.00     the segment. output is the bare shared plate here
frame 3600   2:00.00     the piece proper begins
frame 7200   4:00.00     the piece ends
frame 7349   4:04.97     THE SHARED PLATE CUTS TO BLACK
frame 7499   4:09.97     the segment ends. bare shared plate again
frame 7529   4:10.97     render ends (handles)
```

The ten seconds either side of the piece are the hand-off. The wall grows out of
the untouched shared plate over 1:50–2:00 and dissolves back into it over
4:00–4:10, so the segment can be cut anywhere in those windows and still match
the surfaces on either side.

**The plate does not fade at the end, it cuts** — mean luma 0.506 at frame 7348
and 0.000 at 7349, after strobing between 0.29 and 0.89 for two seconds before
that. The wall's own dissolve is timed to land on that cut rather than on a
clock. An earlier version ran its dissolve on a clock and was still at half
strength when the plate cut, which amplified the strobe and then painted a
treatment onto pure black for five more seconds.

### Format

| | |
|---|---|
| codec | ProRes 422 HQ (10-bit 4:2:2) by default; ProRes 4444, DNxHR HQX and H.264 are also wired up |
| frame rate | 30.000 fps, constant |
| colour | bt709 primaries / transfer / matrix, explicitly tagged |
| naming | `PxDL_SW_SPSW1_03270-07529_MASK-LAYER.mov` — plate, loop frame range, and which mask set was used |

Colour is tagged explicitly because the two source plates disagree about it (one
tagged bt709, one untagged), and an untagged delivery is exactly how a
brightness step at the seam gets introduced.

---

## 2. What the wall does

The noise plate becomes a **sky inside the venue**. Every **opening becomes an
oil field** — thin-film interference, one field per opening rather than one
across the wall. **Polished chrome objects** arrive from outside, in through the
windows, cross the room, and leave through the big middle door.

Back to front:

```
sky                  far behind the wall       moves against the camera
oil                  just behind the surface
fake jambs           inside every opening      the lit side follows the camera
objects (outside)    clipped to their opening
objects (inside)     loose in the room         move with the camera
the two pillars      nearest the viewer        move most, and occlude the objects
```

The pillars take the sky's colour as ambient light (`--pillar-ambient`), so they
go blue at night and hot at last light along with everything else, and the
plate's grain is mostly faded off them (`--pillar-noise`) so the flutes and the
crossing sun can be seen. Both measured in `04_DECISIONS.md`.

A slow left-right camera drives all of it, and each layer moves by a different
amount. On a flat wall, differential motion *is* depth.

### The objects

26 over the two minutes, two or three on screen at a time. All round — spheres,
tori, Möbius torsions and trefoil knots. Hard-edged solids were tried and cut.

Four things make them sit in the room rather than on top of it:

- **They are clipped to their own opening while outside.** Per fragment, against
  the opening's own index in `PxDL_SW_OPENING_ID`, so an object passing through
  window 7 can never flash in window 8. Verified: 0 of 2829 candidate pixels
  leaked.
- **The crossfade is driven by distance to the opening**, not by progress along
  the path, so it fires when the object is actually in the doorway.
- **Depth decides occlusion once per object, not per frame.** An object spans a
  range of depths and the pillar is a plane at one depth, so a per-fragment test
  genuinely slices an object in half when its centre is near that plane —
  correct geometry that reads as the pillar cutting through it. The side is
  chosen from the object's near point and holds for its whole flight.
- **The chrome reflects the same sky the wall shows.** `skyEnv()` in
  `lib_common.glsl` is shared between the background and the metal.
- **The glass reacts when one comes through it.** Each crossing of the wall
  plane is solved once, before the render, and leaves a ring that drags the
  window's leaded bars and chamfers out of shape as it passes — the projection
  deforming the real architecture. Three weights, tuneable and keyframeable:
  `--ripple-warp` (the bend), `--ripple-shade` (the light swing), `--ripple`
  (the colour) and `--ripple-grow` (how fast the disturbed area spreads — it
  starts as a point, the way a drop does). See `04_DECISIONS.md` for which of
  them saturates and what each was measured at.

The camera is **orthographic** — the artist confirmed there is no viewer sweet
spot, people move around the hall, and anamorphic geometry only works from the
position it was built for. Consequence: objects travel across the wall plane
rather than out into the room.

### The noise is the floor, never the ceiling

It runs across all twelve surfaces and is what ties the piece together. There
are **no procedural clouds anywhere**. The plate's own dither is the only
texture on the wall: it drives the sky bands and the oil thickness rather than
sitting behind them, and the plate is sampled **only at its own pixel, never
offset** — so the shared noise is bit-for-bit static under the camera move.
Proved by rendering the flat wall at both camera extremes: 0 pixels differ.

Only the objects stand out.

---

## 3. The mask, and the two sets

31 mattes at full canvas plus a crop per plate, all built by `build_masks.py`
from the supplied colour-coded mask.

```
01 WALL   02 WINDOW   03 BASE   04 DOOR   05 COLUMN   06 TRIM
07 NOMAP          the ONLY non-projected area - keep it black
08 PROJECTABLE    everything except NOMAP   <- multiply the comp by this
09 OPENINGS       windows + doors           <- where 3D emerges
10 FACADE_FLAT    wall + column + trim
```

27 openings (24 windows, 3 doors) and 2 pillars. NOMAP is 6.91 % of the canvas.

The supplied mask has annotation text burned into it — the title on the wall and
`CLOSED DOOR` in each door, 1.7 % of the canvas — which is removed by
nearest-label fill plus edge reconstruction. 472 ragged columns became 0 across
all 18 upper windows, and undamaged windows are untouched.

### Why there are two sets

The shared noise plate is **not a flat field**: the windows, doors and columns
are drawn into it, each with a thin border. So there are two descriptions of the
same facade, and they do not agree everywhere.

Globally they do — sliding the entire authored mask over the plate finds its
best fit at −2 px horizontally, 0 vertically, which is where it already is. The
disagreement is **per shape**, up to 20 px.

| | mean \|dx\| | mean \|dy\| | agreement |
|---|---|---|---|
| `layer` — the authored mask, as drawn | 4.8 | 4.4 | 0.395 |
| `aligned` — the same shapes, moved onto the plate | 3.1 | 2.5 | 0.455 |

*agreement* is how much of the border the plate actually draws that the outline
sits on, 0..1, measured where it is.

**In the aligned set every shape is the authored shape, translated.** Nothing is
redrawn and nothing is deformed. Verified on pixels:

```
29 groups: 21 exact translations, 8 extended along a flat canvas edge
DEFORMED:                                   0
black area (NOMAP) differing from authored: 0 px
COLUMN 0.0 %   TRIM 0.0 %   WINDOW +0.1 %   area change
```

Full account, including the four guards that each caught a real defect, in
`06_MASKS.md`.

**This is an open decision.** `Layer` is the default and nothing changed unless
you choose otherwise. Proofs of both are rendered; put them side by side and
pick. It is an artistic call — the measurements only say which sits closer to
the plate, not which looks better.

---

## 4. Everything that has been measured

### The supplied material

| | |
|---|---|
| noise plates | 0.019 bits/pixel, DCT block-edge ratio 1.22–1.41 — genuinely preview-grade |
| the two supplied plates | differ by a mean of **7.1** through their own 1000 px overlap — 0.0 on black frames, 12.8 at the busiest, 25.0 at the worst sample in the whole loop |
| overlap ramp | none. Column means across all 1000 columns sit at ratio 0.98–1.04 |
| window top edge, in the plate | a 13 / 255 step against the sky |
| window bottom edge | an 8 / 255 step against the masonry |
| plate luma behind a pillar | 78–86 at the capital, 47–58 down the shaft, 58–69 at the base |

### The luminance arc

| time | mean | character |
|---|---|---|
| 2:00 – 3:10 | 0.10 – 0.11 | near-black for seventy seconds. flat, not a ramp |
| 3:10 – 3:24 | 0.28 | the first real brightening |
| 3:24 – 3:40 | 0.49 – 0.53 | the image fills in, high contrast |
| 3:40 – 3:44 | 0.58 | peak |
| 3:44 – 3:52 | 0.25 | hard transition, drops back |
| 3:52 – 4:00 | 0.40 | erratic, strobing, the hardest cuts of the segment |

The first seventy seconds are the whole problem: there is no arc to ride there,
so the first half is carried by the wall's own content. Every cut between 3:22
and 3:44 lands on an exact whole second, so the animation locks to a 30-frame
grid in that window and goes free after 3:51 where the plate breaks the grid.

Per-frame data in `reference/noise_arc.csv`, 4260 rows.

### The delivered files

| codec | overlap difference | tolerance used |
|---|---|---|
| ProRes 4444 | **0.0000** — 4:4:4, exact | 0.5 |
| ProRes 422 HQ | 1.04 | 2.0 |
| DNxHR HQX | 1.62 | 3.0 |
| H.264 | 2.55 | 5.0 |

Both plates are cut from the same frame, so the content is identical by
construction. What the check measures afterwards is the two files having been
*encoded separately at different widths* — different macroblock grids, different
bit allocation — so every lossy codec leaves a little noise there. For scale:
the supplied noise plates differ by a mean of 7.1 through their own overlap, so
these files are several times more consistent through the seam than the material
they sit alongside.

### The pillars

| | cap vs shaft | plinth vs shaft | shaft grain |
|---|---|---|---|
| as it was | +40.3 % | +14.3 % | σ 5.68 |
| trim lift off | +27.9 % | +4.5 % | σ 5.69 |
| **+ plate high-pass** | **+18.2 %** | **+1.8 %** | **σ 5.79** |

Two causes, fixed together: the capital and plinth were being brightened
relative to the shaft, and the wall's own horizontal banding was printing
through a solid object. The grain is untouched, so the plate still drives the
pillar — just not the wall's shape. 0 pixels changed anywhere outside the
pillars.

---

## 5. The export pipeline

```
EXPORT.cmd  →  export_delivery.ps1  →  render_shader.py  →  verify_plates.py
                                                         →  DELIVERY_NOTES.txt
```

`export_delivery.ps1` refuses to start if anything is missing and refuses to
report success if the plates do not verify.

| preset | what | size | time |
|---|---|---|---|
| `Deliver` | full 9788 × 2552, ProRes 422 HQ, two plates | 51 GB | ~2 h |
| `DeliverMax` | full 9788 × 2552, ProRes 4444, two plates | 250 GB | ~2.7 h |
| `Preview` | **the whole wall as one file**, 4894 × 1276 | 1.4 GB | ~9 min |
| `PreviewSmall` | the whole wall as one file, 2446 × 638 | 420 MB | ~3 min |
| `Share` | the whole wall, 1228 × 320, for sending | **under 16 MB** | ~9 min |
| `Draft` | half size, two plates, full length | 1 GB | ~10 min |
| `Proof` | half size, two plates, 25 seconds | 180 MB | ~2 min |

The three preview presets are stitched whatever `-Layout` says: two plates with
a 1000 px overlap is the delivery format, not something anyone can watch. At ÷4
the canvas is 2447 × 638 — an odd width, which no codec will take — so the last
column is dropped and the render says so. That is allowed for an H.264 review
render and still refused for a delivery codec.

`Share` has a hard ceiling rather than a target, so it sets the bitrate from the
frame count (`--fit-mb`) instead of a quality level, and the export fails if the
finished file is over. It renders at ÷2 and scales to 1228 px on the way out
(`--out-width`): at 900 kbit/s the downscale is worth more than the extra
pixels, measured at SSIM 0.931 against 0.896 encoding from ÷4.

Three switches, all with safe defaults:

| | |
|---|---|
| `-Layout` | `Plates` (default) · `Stitched` · `Both`. `Both` gives all three files from one render, for when nobody knows yet what the producer wants |
| `-Masks` | `Layer` (default) · `Aligned`. Separate folders and filenames, so both can be rendered one after the other |
| `-HQ` | use the high-quality masters in `source_hq` instead of the preview mp4s. Runs `check_hq.py` as part of preflight |

Preflight checks python, moderngl, numpy, ffmpeg, the required encoder, both
source files, all 31 masks and the reference assets for the chosen set, that
`noise_arc.csv` covers the frame range, and free disk against a measured
per-frame size.

The render runs at **below-normal priority** with a capped ffmpeg thread count,
so the machine stays usable. An earlier version at normal priority made the
desktop stop redrawing and looked like a hang.

---

## 6. What is verified, and what each check caught

Only checks that measure rendered pixels have ever caught a real bug here. This
is the list, and what each one found.

| check | what it caught |
|---|---|
| overlap comparison | the supplied plates, encoded independently, differ by a mean of 7.1 through their own overlap; sliced from one frame, 0.0000 |
| per-codec tolerance | a hardcoded 2.0 failed a perfectly good Draft; H.264 measures 2.55 |
| flat-wall difference at both camera extremes | proved the shared noise is static — 0 px |
| opening-ID clipping | objects flashing in neighbouring windows; 0 of 2829 px leak now |
| opening-ID map at reduced scale | ffmpeg's `neighbor` scaler is not nearest: 84 indices where 28 exist, and the per-opening UV smoothed with them. Previews only; `--div 1` was always exact |
| door frame containment | treating a door's whole opening as door changes 4,965 px, 0 of them outside a door |
| pillar containment | the pillar shading fix changed 0 px outside the pillars |
| shape-integrity check on the aligned masks | the first mask attempt deformed 25 of 29 shapes and ate 3 % of the black area |
| frame-alignment search in `check_hq.py` | not yet triggered — it is there because a one-frame offset in the masters would put this wall out of step with eleven other surfaces while looking perfectly fine on its own |
| progress-bar replay | 5 updates, all carriage-return prefixed, 0 newlines, 1 terminal line |

Two mistakes worth remembering, because both looked like art problems and were
actually measurement problems:

- **"The chrome looks transparent."** `uRoomMix` was sampling the plate per
  pixel and stamping the wall's pattern onto the metal.
- **"There is a shadow across the pillar."** The same thing again, one layer
  further forward — the wall's own luminance banding printing through a solid
  object.

---

## 7. Open decisions and risks

**Decisions that are yours**

1. **Which mask set ships.** `Layer` or `Aligned`. Proofs of both exist. See
   `06_MASKS.md`.
2. **Two plates or one stitched file.** `-Layout Both` covers it if the producer
   has not said.
3. **Codec.** ProRes 422 HQ is the default; 4444 is bit-exact through the
   overlap if there is disk for it.

**To ask the producer**

1. **The C4D toolkit** (`TOOLKIT_3D_PxDL_MAINEXPO_v4.c4d`) — it gives the real
   wall dimensions and UV layout. The door-height argument puts the canvas near
   10 m tall, but that is inference, not measurement.
2. **Which codec and which layout** they actually want.
3. **Whether they can supply the noise as ProRes** rather than uncompressed —
   genuinely uncompressed at 7200 × 2552 is about 1.1 GB/s to read both plates
   at 30 fps and roughly 660 GB per plate for the full loop.

**Risks that are still open**

- **The shaders have never been seen on the wall.** Everything about brightness,
  contrast and dither pitch is judged on a monitor. A facade is not a monitor.
  Do the projection test before committing to the full render.
- **Physical scale is unconfirmed.** See above.
- **The final render is still against the preview mp4s.** They are 0.019
  bits/pixel and the plate is now *driving* the image rather than sitting behind
  it, so the compression shows more than it used to.

---

## 8. When the high-quality noise arrives

Four steps, and the only one that takes real time is the last.

```powershell
# 1. paths into project.json -> source_hq
#    either  "STITCHED": "E:/PxDL/masters/PxDL_SW_noise_9788x2552.mov"
#    or      "SPSW1": "...", "SPSW2": "..."

# 2. validate them - about 30 seconds, must say READY
python _pipeline\scripts\check_hq.py

# 3. remeasure the arc if it says to - about 4 minutes
python _pipeline\scripts\analyse_arc.py --hq

# 4. re-align the masks to the better source - about 12 minutes
del _pipeline\reference_aligned\PxDL_SW_NOISE_STATIC_9788x2552.png
python _pipeline\scripts\build_masks.py --align --hq
python _pipeline\scripts\compare_masks.py

# then export
.\_pipeline\scripts\export_delivery.ps1 -Preset Deliver -HQ
```

`check_hq.py` answers four questions, and the second is the one that matters:

- **Geometry** — 7200 × 2552 / 3588 × 2552, or 9788 × 2552 stitched; 30 fps;
  18000 frames.
- **Frame alignment** — it decodes the same frame from the mp4 and from the
  master at offsets −3…+3 and reports which actually matches. It must be 0.
- **Levels** — a master is very often tagged full-range where the mp4 was
  limited. That shifts every luma value, and since the plate drives the image it
  changes the look, not just the brightness.
- **The arc** — whether `noise_arc.csv` needs remeasuring.

Both the stitched and the two-plate forms are handled; `STITCHED` wins if both
are filled in.

---

## 9. What changed most recently

Recorded because it explains why several things are named what they are.

**The pillars had a shadow lying across them.** Two causes, found by measuring
the rendered frame rather than by looking at it. The capital and plinth were
being brightened relative to the shaft (`--trim-lift`, now 0), and the wall's own
horizontal banding was printing through a solid object (`--plate-hp`, now on).
Cap went from +40 % of the shaft to +18 %, plinth from +14 % to +2 %, grain
untouched, and 0 pixels changed outside the pillars. `04_DECISIONS.md`.

**The second mask set was rebuilt from scratch.** The first version traced
outlines out of the plate and they wobbled; it also ate 3 % of the black area.
It now translates the authored shapes as rigid pieces instead — 0 deformed, 0 px
of NOMAP changed. Renamed from `noise` to `aligned` throughout, because "traced"
was no longer true. `06_MASKS.md`.

**The progress bar rewrites one line.** It always did; the export script was
piping it through `Tee-Object`, and PowerShell treats a carriage return as a
line break. The renderer now writes its own log through `--log` and is not piped.

**Two plates or one stitched file, and the same for the source.** `-Layout
Plates|Stitched|Both` on output; `source_hq.STITCHED` or the `SPSW1`/`SPSW2` pair
on input. Nobody has to know yet which the producer wants.

**Per-codec overlap tolerance.** A hardcoded 2.0 was failing perfectly good
H.264 drafts, which measure 2.55. `05_DELIVERY.md` has the table.

**Two documentation corrections worth knowing about**, because both were quoted
as facts and neither was right:

- the supplied plates' overlap difference was given as 1.54 in one document and
  11.7 in another. Measured properly it is a distribution, not a number: mean
  7.1 across this segment, 0.0 where the plate is black, 12.8 at the busiest.
  11.7 turned out to be the value at frame 6000 specifically.
- the Draft and Proof presets under-estimated their own disk use by 2×. Measured
  and corrected.

---

## 10. Where everything is

```
EXPORT.cmd                   double-click this to deliver
_pipeline/
  project.json               every constant. the scripts read it, so edit it here
  masks/                     31 authored mattes, full canvas + per-plate
  masks_aligned/             the same 31, moved onto the noise plate
  reference/                 openings.json, facade_regions.*, opening ID + SDF,
                             noise_arc.csv, canvas_layout.png
  reference_aligned/         the same for the aligned set, plus the averaged
                             plate and the mask-comparison overlay
  scripts/                   see README.md for what each one is for
  shaders/                   sky_oil.frag, chrome_object.*, pillars.frag,
                             lib_common.glsl (shared hash/noise/fresnel/thin-film)
  docs/                      you are here
```

`noise_arc.csv` lives only in `reference/`. It is measured off the plate and has
nothing to do with which mask set is in use, so the aligned set does not carry a
copy and the renderer falls back to the shared folder for it.
