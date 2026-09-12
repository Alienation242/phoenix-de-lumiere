# Two masks, and which one is right

There are two descriptions of this facade in the project. They disagree, and
the disagreement is small but real. This is what was measured and what to do
about it.

```powershell
# build the second set (about 12 minutes, once - most of it is averaging
# the plate, which is then cached and reused)
python _pipeline\scripts\build_masks.py --align

# see how they compare
python _pipeline\scripts\compare_masks.py

# render against either
.\_pipeline\scripts\export_delivery.ps1 -Preset Proof -Masks Layer
.\_pipeline\scripts\export_delivery.ps1 -Preset Proof -Masks Aligned
```

Each export goes to its own folder and every file carries `_MASK-LAYER` or
`_MASK-ALIGNED` in its name, so both can be rendered one after the other
without overwriting anything.

---

## The thing that started this

The shared noise plate is not a flat field. **The windows, doors and columns are
already drawn into it**, each with a thin border. So the plate itself is a
description of the facade — and the authored colour-coded mask is a second one.

Measured on the plate, the borders are faint:

| edge | step |
|---|---|
| top of a window, against the sky above | 13 / 255 |
| bottom of the same window, against the masonry | 8 / 255 |

Faint, but perfectly consistent — average 164 frames spread across the ten
minute loop and the dither cancels out, leaving the architecture standing there.
That average is saved as `reference_aligned/PxDL_SW_NOISE_STATIC_9788x2552.png`;
it is worth opening once, because everything below is measured against it.

---

## Is the mask off, or is the noise off?

Neither, globally. Sliding the *entire* authored mask over the plate and looking
for the offset that lines it up best lands on **−2 px horizontally, 0 px
vertically** — which is to say, where it already is. There is no systematic
shift, no scale error, no crop mistake. Whoever built the two built them from
the same source.

The disagreement is **per shape**. Some windows are on the money; others are out
by up to 20 px. Window 9 is 11 px left of where the plate draws it; windows 22,
23 and 24 are 8–20 px out; door 25 is 10 px low.

```
            mean |dx|  mean |dy|  agreement
authored         4.8        4.4      0.395
aligned          3.1        2.5      0.455
```

*agreement* is how much of the border the plate actually draws that the outline
sits on, where it is, 0..1. Higher is better. Run `compare_masks.py` for the
per-opening table and an overlay image
(`reference_aligned/PxDL_SW_MASK_COMPARE_*.png`, authored in red, aligned in
green).

### Does 12 px matter?

It did not when the plate sat *behind* the treatment. It does now that the plate
**drives** it. An oil field 12 px wider than its window spills a rim of colour
onto the masonry, and an object clipped to an opening crosses a wall that is not
where the plate says the wall is.

---

## How the aligned set is built

**Every shape is the authored shape, translated. Nothing is redrawn and nothing
is deformed.** Straight edges stay straight, arches stay arches, the stepped
capital on each pillar is the authored one, and the black NOMAP area is not
touched at all.

1. **Average the plate.** 164 usable frames across the loop; the dither cancels,
   the architecture stays. Frames where the plate has cut to black are skipped.
2. **Find the border lines.** Gradient of the averaged plate, then two filters:
   - *hysteresis*, as in Canny — a strong threshold says where an edge certainly
     is, a much weaker one says how far it runs. Without this the 8/255 bottom
     edges break into a dozen fragments.
   - *length* — the plate's own cloud texture is just as strong as the
     architecture but blobby. Anything that does not run for 120 px is thrown
     away. 238,364 pieces of ridge; 95 are architecture.
3. **Group what must move together.** An opening is a component of
   `WINDOW|DOOR`, so a door keeps the strips that are its frame and its leaf. A
   pillar is a component of `COLUMN|TRIM`, so shaft, capital and plinth are one
   stone object. 27 openings and 2 pillars — the same 27 `openings.json` has
   always counted.
4. **Find each group's offset**, maximising how much of the plate's border line
   its outline sits on, over ±28 px.
5. **Rebuild**: lift the moving shapes off, put them down where they belong, let
   the wall close over the sliver behind them.

### The four guards, and what each one cost to learn

- **A prior toward staying put.** The authored position is good evidence, so the
  further a shape wants to move the more it has to gain. Without it, door 27 —
  whose top edge sits in a band of horizontal lines where every offset scores
  about the same — wandered 19 px down on a 9 % gain.
- **Nothing moves into the black area.** Window 24 sits flush against NOMAP;
  left alone it moved 20 px right and lost 20 px off its own side to the clip.
  It now keeps its shape and gives up the sideways move instead.
- **Nothing lands on anything else.** Window 22 wanted 8 px right, which put its
  edge under pillar 2 and ate 7990 px of it. Every shape's ground is claimed
  before any of them move, and a shape gives up distance rather than take a bite
  out of a neighbour.
- **Shapes flush against a canvas edge are re-anchored to it.** All three doors
  reach the bottom of the canvas; moving one up 10 px would leave a 10 px sliver
  of wall under it. Their edge there is dead straight, so extending it back to
  the boundary changes the shape not at all.

### Verified, on pixels

```
29 groups: 21 exact translations, 8 extended along a flat canvas edge
DEFORMED: 0
black area (NOMAP) differing from authored: 0 px
COLUMN, TRIM: 0.0 % area change.   WINDOW: +0.1 %
```

`scripts/compare_masks.py` re-measures the registration; the shape check above
is worth repeating if the plate is ever replaced.

### What this replaced

The first attempt traced the outlines out of the plate directly — re-growing
every boundary onto the plate's border lines. It landed in the right place and
**wobbled**, because a boundary grown one pixel at a time follows every wrinkle
in a 0.019 bits/pixel mp4. On a facade that has to line up to the pixel, a
wobbly edge in the right place is worse than a clean edge a few pixels out. It
also ate 3 % of the black area, which is the one region that must not move.

---

## Which one to send

**Default is `Layer`. Nothing changed unless you choose otherwise.**

| | for | against |
|---|---|---|
| `Layer` | the authored mask, untouched | up to 20 px away from what the plate draws |
| `Aligned` | identical shapes, sitting where the plate says | four shapes had to give up some or all of their move to keep their shape |

Render `Proof` with each, put them side by side, and pick. Since the shapes are
now identical in both, the only difference on screen is *where* a few of them
sit — which is exactly the question worth looking at.

### One thing to expect

The objects are fitted to the openings they fly through, so they take their
positions from whichever mask set is in use. The **same** objects appear in the
same order at the same times — the seed and the schedule do not change — but
each is placed against its own opening, so a few move by a handful of pixels.

---

## When the high-quality noise arrives

Re-align to it. The masters have more of the border to see than a
0.019 bits/pixel mp4 does.

```powershell
python _pipeline\scripts\build_masks.py --align --hq
python _pipeline\scripts\compare_masks.py
```

`--hq` reads whatever `source_hq` points at — one stitched file or the two
plates — the same as everything else. Delete
`reference_aligned/PxDL_SW_NOISE_STATIC_*.png` first, or it will reuse the
average it already has.

---

## Files

```
masks_aligned/      the 31 mattes, same names as masks/, aligned
reference_aligned/  openings.json, facade_regions.*, opening ID + SDF
                    PxDL_SW_NOISE_STATIC_*   the averaged plate
                    PxDL_SW_MASK_COMPARE_*   both outlines over it
```

`noise_arc.csv` is **not** duplicated: it is measured off the plate and has
nothing to do with which mask set is in use. The renderer falls back to the
shared `reference/` folder for it.
