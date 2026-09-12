# Phoenix de Lumière — SW wall — pipeline kit

You own the `SW` surface: one of twelve mapped surfaces in a 62 × 38 m hall.
Your segment is 2:00–4:00 of the ten-minute loop, delivered as two videos.
Ten seconds either side (1:50–2:00, 4:00–4:10) are the hand-off, where the wall
grows out of the shared noise plate and dissolves back into it.

**To deliver: double-click `EXPORT.cmd` in the project root and pick a number.**
Everything else here is for when something is unusual.

Everything here was derived by measuring the supplied files.

```
docs/07_STATUS.md            ← START HERE. what exists, what is verified, what is open
docs/00_TECHNICAL_SPEC.md    what the supplied files actually are
docs/01_PIPELINE.md          how the thing is built and rendered
docs/02_PORTABLE_RENDER.md   moving to another machine / render node
docs/03_48H_PLAN.md          the original plan. historical
docs/04_DECISIONS.md         why things are the way they are
docs/05_DELIVERY.md          handing the files over. the producer reads this
docs/06_MASKS.md             the two mask sets, and which one is right

project.json                 every constant. the scripts read it, so edit it here
masks/                       31 cleaned region mattes, full canvas + per-plate
masks_aligned/               the same 31, moved onto the shared noise plate
reference/                   canvas_layout.png        geometry diagram for the producer
                             openings.json            all 27 windows and doors
                             PxDL_SW_OPENING_ID_*     R=opening index, GB=local UV
                             PxDL_SW_OPENING_SDF_*    distance from opening edges
                             facade_regions.json/csv  every region rectangle
                             noise_arc.csv            per-frame luma + motion
reference_aligned/           the same, for the aligned set, plus
                             PxDL_SW_NOISE_STATIC_*   the plate with the dither
                                                      averaged away - the
                                                      architecture it draws
                             PxDL_SW_MASK_COMPARE_*   both outlines over it
scripts/     render_shader.py  ← renders the wall. no licence, any resolution
             + check, build, extract, slice, encode, verify, preview
shaders/     lib_common.glsl  hash/noise/fbm/fresnel/thin-film + skyEnv(), shared
                              by the wall and by the chrome. #include'd.
             sky_oil.frag     banded sky, per-opening oil, fake jambs
             chrome_object.*  polished metal, reflecting skyEnv()
             pillars.frag     the two column bays, as the nearest layer
             psx_*.glsl       the earlier PS1 treatment, kept as a fallback
```

---

## The five things that matter

**1. The two videos overlap by 1000 px.** Not two halves.
`SPSW1 = x 0..7200`, `SPSW2 = x 6200..9788`, canvas `9788 x 2552` = the mask.

**2. The overlap is not pre-blended.** Both plates carry full brightness there.
Render one master and slice it — `make_delivery.ps1` does it in one pass.

**3. The noise is the floor, never the ceiling.** It runs across all twelve
surfaces and is what ties the piece together. Composite over it, never replace
it, never out-brighten it, and drive your parameters from its arc.

**4. Only black is non-projection.** Every other colour in the mask is a
projection surface — the colours describe surface type, not whether to light it.
Multiply your comp by `MASK_08_PROJECTABLE` as the last step.

**5. TouchDesigner is out for rendering.** Non-Commercial refuses to output above
1280×1280 and you do not have a commercial licence. `scripts/render_shader.py`
replaces it: the same GLSL on a plain OpenGL 3.3 context, any resolution, no
licence of any kind. A full 9788×2552 frame costs ~0.14 s even on the Intel iGPU.
Free DaVinci Resolve has the same problem (caps at 3840×2160) — you do not need
it either.

---

## Quick start

**Delivering.** Double-click `EXPORT.cmd` and pick a number. It checks the
machine, renders, verifies the plates and writes the notes that go with them.
It refuses to start if anything is missing and refuses to report success if the
plates do not verify. Run `Proof` (two minutes) before `Deliver`.

**Looking at something quickly.**

```powershell
cd _pipeline\scripts

# 20 s at quarter size, straight to an mp4
python render_shader.py --div 4 --start 4800 --count 600 --mp4

# is this machine ready? (run it on any render node too)
.\check_environment.ps1

# point the big folders at an external drive
$env:PXDL_RENDER_ROOT  = "E:\PxDL\render"
$env:PXDL_DELIVER_ROOT = "E:\PxDL\deliver"
$env:PXDL_WORK_ROOT    = "E:\PxDL\work"
```

**Rebuilding the derived data** — only needed if the source mask or the noise
plate changes.

```powershell
python analyse_arc.py            # per-frame luma and motion -> noise_arc.csv
python build_masks.py            # the 31 mattes + region tables + ID map + SDF
python build_masks.py --align    # the second, plate-aligned mask set
python compare_masks.py          # how far each one is from the plate
```

---

## Scripts

**The delivery path** — these four are what actually ships the work.

| | |
|---|---|
| `export_delivery.ps1` | **the one to run.** Preflight → render → verify → notes. `EXPORT.cmd` in the project root is a double-clickable wrapper |
| `render_shader.py` | **renders the wall** — sky/oil + chrome objects + pillars over the noise, any resolution, no licence. Writes the two projector plates directly |
| `verify_plates.py` | resolution, frame count, and that the 1000 px overlap holds. The export runs it for you and will not report success without it |
| `check_hq.py` | validates the high-quality masters when they arrive: geometry, **frame alignment**, levels, and whether the arc needs remeasuring |

**Building the derived data** — run when the source mask or the plate changes.

| | |
|---|---|
| `build_masks.py` | the 31 mattes, region tables, opening ID map and SDF. `--align` builds the second, plate-aligned set |
| `compare_masks.py` | measures both mask sets against the plate and draws the overlay |
| `analyse_arc.py` | per-frame luma and motion → `noise_arc.csv`, and prints the cut list |

**Utilities** — from before the renderer existed; still useful, not in the
delivery path.

| | |
|---|---|
| `check_environment.ps1` | VRAM, disk, encoders, licences, source files. Run first on any machine |
| `extract_segment.ps1` | pulls the segment out of the plates at ÷1, ÷2 or ÷4, per-plate or stitched |
| `master_to_plates.ps1` | master sequence → lossless 16-bit plate sequences |
| `make_delivery.ps1` | master sequence → encoded plates in one pass |
| `render_test.ps1` | small preview video of anything rendered, frame numbers burnt in |
| `preview_stitch.ps1` | stitches the plates back to one canvas for review |
| `_common.ps1` / `_common.py` | path and ffmpeg resolution — nothing machine-specific anywhere else |

---

## The story, as it stands

The noise becomes a **sky inside the venue**; every **opening becomes an oil
field**. **Polished chrome objects** — spheres, tori, Möbius torsions and
trefoil knots, all round, no hard-edged solids — arrive **from outside, in
through the windows**, cross the room, and **leave through the big middle door**
(door 26, x 4696, 757 × 754 — the largest of the three and the one nearest the
canvas centre). 26 of them over the two minutes, two or three on screen at once.

There are no procedural clouds anywhere. The plate's own dither is the only
texture on the wall: it drives the sky bands and the oil thickness rather than
sitting behind them. Only the objects stand out.

The whole thing is layered for depth, and a slow left-right camera moves each
layer by a different amount. On a flat wall, differential motion *is* depth:

```
sky + clouds        far behind the wall      moves against the camera
oil in the openings just behind the surface
fake jambs          inside every opening     the side you can see follows the camera
chrome objects      in the room              move with the camera
the two pillars     nearest the viewer       move most, and occlude the objects
```

Two details do most of the work:

**Objects are clipped to the opening while they are outside it.** Beyond the
wall they are drawn only where a window or the door is, so they genuinely read
as coming in from outside and going back out, rather than fading up on the wall
surface. The crossfade is driven by *distance to the opening*, not by progress
along the path, so it fires when the object is actually in the doorway.

**The chrome reflects the same sky the wall is showing.** `skyEnv()` in
`shaders/lib_common.glsl` is shared between the background and the metal, so the
objects mirror this venue's clouds, moving at this venue's speed. That shared
world is what makes them sit in the room instead of on top of it.

## Still to ask the producer

1. **The C4D toolkit** (`TOOLKIT_3D_PxDL_MAINEXPO_v4.c4d`) — gives you the real
   wall dimensions and UV layout. Your 6 m estimate is too low; the doors put the
   canvas at ~10 m tall (spec §6).
2. **Codec preference** — offer ProRes 4444 plus the 16-bit master on the drive.
3. **Handles** — the render covers 3270–7529; the piece proper is 3300–7499,
   with 1:50–2:00 and 4:00–4:10 as hand-off windows that can be cut anywhere.
4. **Stitched or two plates?** `-Layout Both` produces both from one render if
   they are not sure — see `docs/05_DELIVERY.md`.

## Working vs final quality

Design against the supplied mp4s — they are small and fast, which is what you
want for the next 40 hours. They are also **0.019 bits/pixel with measurable DCT
blocking**, so the **final render must use your ProRes masters**. Put their paths
into `project.json` → `source_hq` and pass `-HQ`.
