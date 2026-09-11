# Phoenix de Lumière — SW wall — pipeline kit

You own the `SW` surface: one of twelve mapped surfaces in a 62 × 38 m hall.
Your segment is 2:00–4:00 of the ten-minute loop, delivered as two videos.

Everything here was derived by measuring the supplied files.

```
docs/03_48H_PLAN.md          ← START HERE. hour-by-hour, and what to cut
docs/00_TECHNICAL_SPEC.md    what the files actually are
docs/01_PIPELINE.md          how to build and render it
docs/02_PORTABLE_RENDER.md   moving to another machine / render node

project.json                 every constant. the scripts read it, so edit it here
masks/                       31 cleaned region mattes, full canvas + per-plate
reference/                   canvas_layout.png        geometry diagram for the producer
                             openings.json            all 27 windows and doors
                             PxDL_SW_OPENING_ID_*     R=opening index, GB=local UV
                             PxDL_SW_OPENING_SDF_*    distance from opening edges
                             facade_regions.json/csv  every region rectangle
                             noise_arc.csv            per-frame luma + motion
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

```powershell
cd _pipeline\scripts

# is this machine ready? (run it on any render node too)
.\check_environment.ps1

# point the big folders at an external drive
$env:PXDL_RENDER_ROOT  = "E:\PxDL\render"
$env:PXDL_DELIVER_ROOT = "E:\PxDL\deliver"
$env:PXDL_WORK_ROOT    = "E:\PxDL\work"

# quarter-res working plate to design against
.\extract_segment.ps1 -Div 4

# per-frame arc -> reference\noise_arc.csv
python analyse_arc.py
```

When the master is rendered:

```powershell
.\make_delivery.ps1 -MasterPattern "E:\PxDL\render\master\PxDL_SW_master.%05d.png"

python verify_plates.py --a "E:\PxDL\deliver\PxDL_SW_SPSW1_3570-7229.mov" `
                        --b "E:\PxDL\deliver\PxDL_SW_SPSW2_3570-7229.mov" --tol 2
```

---

## Scripts

| | |
|---|---|
| `check_environment.ps1` | VRAM, disk, encoders, licences, source files. Run first on any machine. |
| `build_masks.py` | rebuilds all 31 masks, region tables, opening ID map and SDF from the source mask |
| `extract_segment.ps1` | pulls your segment out of the plates at ÷1, ÷2 or ÷4, per-plate or stitched |
| `analyse_arc.py` | per-frame luma and motion → `noise_arc.csv`, and prints the cut list |
| `master_to_plates.ps1` | master → lossless 16-bit plate sequences |
| `make_delivery.ps1` | master → encoded plates in one pass (ProRes/DNxHR/FFV1/H.264/PNG16) |
| `render_shader.py` | **renders the wall** — sky/oil + chrome objects + pillars over the noise, any resolution, no licence |
| `render_test.ps1` | small preview video of anything you have rendered, frame numbers burnt in |
| `verify_plates.py` | resolution, frame count, and that the overlap is identical. **Always run this.** |
| `preview_stitch.ps1` | stitches the plates back to one canvas for review |
| `_common.ps1` / `_common.py` | path and ffmpeg resolution — nothing machine-specific anywhere else |

---

## The story, as it stands

The noise becomes a **sky with clouds on the inside of the venue**; every
**opening becomes an oil field**. **Polished chrome objects** — spheres, tori,
cubes, diamonds, pyramids — arrive **from outside, in through the windows**,
cross the room, and **leave through the big middle door** (door 26, x 4696,
757 × 754 — the largest of the three and the one nearest the canvas centre).

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
3. **Handles** — 3570–7229 (±1 s) is the current default. Drop them if time runs short.

## Working vs final quality

Design against the supplied mp4s — they are small and fast, which is what you
want for the next 40 hours. They are also **0.019 bits/pixel with measurable DCT
blocking**, so the **final render must use your ProRes masters**. Put their paths
into `project.json` → `source_hq` and pass `-HQ`.
