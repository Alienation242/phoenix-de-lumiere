# Two masks, and which one is right

There are two descriptions of this facade in the project. They disagree, and
the disagreement is small but real. This is what was measured and what to do
about it.

```powershell
# build the second set (about 12 minutes, once - most of it is averaging
# the plate, which is then cached and reused)
python _pipeline\scripts\build_masks.py --from-noise

# see how they compare
python _pipeline\scripts\compare_masks.py

# render against either
.\_pipeline\scripts\export_delivery.ps1 -Preset Proof -Masks Layer
.\_pipeline\scripts\export_delivery.ps1 -Preset Proof -Masks Noise
```

Each export goes to its own folder and every file carries `_MASK-LAYER` or
`_MASK-NOISE` in its name, so both can be rendered one after the other without
overwriting anything.

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
That average is saved as `reference_noise/PxDL_SW_NOISE_STATIC_9788x2552.png`;
it is worth opening once, because everything below is measured against it.

---

## Is the mask off, or is the noise off?

Neither, globally. Sliding the *entire* authored mask over the plate and looking
for the offset that lines it up best lands on **−2 px horizontally, 0 px
vertically** — which is to say, where it already is. There is no systematic
shift, no scale error, no crop mistake. Whoever built the two built them from
the same source.

The disagreement is **per opening**. Some windows are on the money; others are
out by up to 24 px:

```
            mean |dx|  mean |dy|  agreement   worst
authored         4.8        4.4      0.395    opening 19, 32 px out
traced           1.2        0.6      0.502    opening 19, 13 px out
```

- *agreement* is how much of the border the plate actually draws that the
  outline sits on, where it is, 0..1. Higher is better.
- 21 of the 27 openings in the traced set sit exactly on the plate's border.
  In the authored set, 2 do.
- The badly-placed ones in the authored set are windows **8, 9, 10, 13, 22, 23
  and 24** — number 24 is 24 px out in x, number 9 is 12 px out.

Run `compare_masks.py` for the full per-opening table and an overlay image
(`reference_noise/PxDL_SW_MASK_COMPARE_*.png`, authored in red, traced in
green).

### Does 12 px matter?

It did not when the plate sat *behind* the treatment. It does now that the plate
**drives** it. An oil field 12 px wider than its window spills a rim of colour
onto the masonry, and an object clipped to an opening crosses a wall that is not
where the plate says the wall is.

---

## How the traced set is built

It does **not** try to work out what each shape means. The plate knows where the
edges are; only the authored mask knows a door from a window. So the labels are
taken from the authored mask and only the boundaries move.

1. **Average the plate.** 164 usable frames across the loop; the dither cancels,
   the architecture stays. Frames where the plate has cut to black are skipped.
2. **Find the border lines.** Gradient of the averaged plate, then two filters:
   - *hysteresis*, as in Canny — a strong threshold says where an edge certainly
     is, a much weaker one says how far it runs. Without this the 8/255 bottom
     edges break into a dozen fragments.
   - *length* — the plate's own cloud texture is just as strong as the
     architecture but blobby. Anything that does not run for 120 px is thrown
     away. 238,364 pieces of ridge; 95 are architecture.
3. **Keep a core of every region as a seed** — everything more than 44 px from
   its own boundary, plus its medial axis, so a 30 px mullion between two
   windows still seeds and the two cannot merge into one.
4. **Re-grow the gaps, blocked by the border lines.** A boundary with a border
   near it lands on that border. A boundary with none — wall against ground —
   meets in the middle, exactly where it already was.
5. **Majority-smooth** to take the staircase off a boundary that was grown a
   pixel at a time.

1.74 % of the canvas changes label. Everything else is untouched.

### What it cost to get right

The first version leaked: a window with an undetected bottom edge grew a
triangular ear out of its own corner, because the growth found a gap and went
through it. The fix was the hysteresis in step 2 — detect the weak bottom edges
and the window stops where it should. Worth knowing if the plate is ever
replaced and the trace has to be tuned again.

---

## Which one to send

**Default is `Layer`. Nothing changed unless you choose otherwise.**

| | for | against |
|---|---|---|
| `Layer` | clean authored geometry — straight edges, even arches | up to 24 px away from what the plate draws |
| `Noise` | sits on the plate; 21 of 27 openings exact | inherits the plate's own slightly irregular edges |

The honest summary: **the traced set is measurably closer to the shared plate,
and the plate is the thing all twelve surfaces have in common.** The authored
set is geometrically tidier. Render `Proof` with each, put them side by side,
and pick — it is an artistic call, not a technical one, and the measurements
above only say which is closer, not which looks better.

The three doors are the one place where the numbers do not clearly favour the
trace: door 26 improves, 25 and 27 measure slightly worse while looking
identical on screen. They sit in a busy band at the bottom of the canvas where
the score is noisy for both.

### One thing to expect

The objects are fitted to the openings they fly through, so they take their
sizes from whichever mask set is in use. The **same** objects appear in the same
order at the same times — the seed and the schedule do not change — but each is
sized and placed against its own opening, so they move by a few pixels between
the two renders. Nothing in the choreography is lost by switching.

---

## When the high-quality noise arrives

Re-trace it. The masters have more of the border to see than a 0.019 bits/pixel
mp4 does.

```powershell
python _pipeline\scripts\build_masks.py --from-noise --hq
python _pipeline\scripts\compare_masks.py
```

`--hq` reads whatever `source_hq` points at — one stitched file or the two
plates — the same as everything else. Delete
`reference_noise/PxDL_SW_NOISE_STATIC_*.png` first, or it will reuse the
average it already has.

---

## Files

```
masks_noise/        the 31 mattes, same names as masks/, traced
reference_noise/    openings.json, facade_regions.*, opening ID + SDF
                    PxDL_SW_NOISE_STATIC_*   the averaged plate
                    PxDL_SW_MASK_COMPARE_*   both outlines over it
```

`noise_arc.csv` is **not** duplicated: it is measured off the plate and has
nothing to do with which mask set is in use. The renderer falls back to the
shared `reference/` folder for it.
