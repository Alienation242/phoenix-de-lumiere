# Phoenix de Lumière — SW wall

Content for one surface of a twelve-surface projection mapping inside a
62 × 38 m exhibition hall. This repo holds the **SW wall**: the 2:40–4:00 slice
of a ten-minute, 30 fps loop, delivered as two video plates.

![canvas layout](_pipeline/reference/canvas_layout.png)

Everything here was derived by measuring the supplied files rather than assuming.

---

## ⚠ A fresh clone cannot render yet

The two noise plates are **gitignored** — 0.80 GB and 0.79 GB, far over GitHub's
100 MB file limit. The clone has the mask, the venue reference and everything
generated from them, but not the video.

```
RawNoise/
  3d room mask.png                     ✓ in the clone
  PxDL_SW_9788x2552 (0-00-00-00).png   ✓ in the clone
  PxDL_SW_SPSW1.mp4                    ✗ copy it in  (0.80 GB)
  PxDL_SW_SPSW2.mp4                    ✗ copy it in  (0.79 GB)
```

Either copy the two mp4s into `RawNoise/`, or point
`_pipeline/project.json` → `source_hq` at the ProRes masters and pass `--hq`,
in which case you never need the mp4s on that machine at all.

`work/`, `render/` and `deliver/` are gitignored too — they are regenerated
output and never travel.

---

## Setup on a new machine

```powershell
python -m pip install moderngl

cd _pipeline\scripts
.\get_ffmpeg.ps1                     # a full ffmpeg into _pipeline\bin (~106 MB)
.\check_environment.ps1              # VRAM, disk, ffmpeg, source files

# point the big folders at fast storage
$env:PXDL_WORK_ROOT    = "E:\PxDL\work"
$env:PXDL_RENDER_ROOT  = "E:\PxDL\render"
$env:PXDL_DELIVER_ROOT = "E:\PxDL\deliver"

# 30 seconds at quarter size, straight to a small MP4
python render_shader.py --div 4 --start 5700 --count 900 --mp4 --preview-width 1224
```

You also need a **full ffmpeg build**, and there is a script for it:

```powershell
.\get_ffmpeg.ps1
```

It fetches the official Windows build, verifies it against the publisher's
SHA-256 and drops it in `_pipeline\bin\`, which every script checks before
`PATH`. Nothing is installed and no admin rights are needed. The binary is
~100 MB so it is gitignored — **run this on every machine you clone to.**

Without it there is no ProRes and no H.264 encoder: every delivery preset stops
at preflight, and previews fall back to mpeg4 — the full 80-second preview came
out at 49 MB that way, where x264 at the same size would be a small fraction of
it.

**If the machine has no working internet** (`getaddrinfo failed`,
`Could not resolve host` — broken DNS, which happened twice on this job), do
this on a machine that does:

```powershell
.\make_offline_bundle.ps1
```

then copy the `_offline_bundle` folder it makes across and double-click
`INSTALL.cmd` inside it. It carries the python wheels and ffmpeg, installs both
without touching the network, and tells you what is still missing. See
`docs/02_PORTABLE_RENDER.md`.

Nothing in the pipeline needs a licensed application.

---

## Tuning the look

**Double-click `TUNE.cmd`.** A page opens in your browser with sliders for
colour, contrast, the noise plate, the oil and the dither. Move one and the wall
re-renders — a real frame through `render_shader.py`, about three seconds at
quarter size, so what you see is what the delivery makes.

- **Save** writes `_pipeline/look.json`. `render_shader.py` loads that as its
  *defaults*, so **`EXPORT.cmd` renders your look** with nothing else to
  remember. It prints `look  N setting(s) from …` when it does.
- Command-line flags still beat the file, and `--no-look` ignores it.
- **◆ keyframes a setting.** Once it has one key it turns amber, and its
  slider then writes a key wherever the playhead is — move the frame, move the
  slider, that is a second key. A small graph under the row draws the curve
  across the whole segment. Between keys you get `hold`, `linear`, `smooth`,
  `ease-in` or `ease-out`, per track or per key, so one transition can snap
  while the next one glides. The look can move over the eighty seconds instead
  of being one setting for all of it.
- **1:1 detail** shows an unscaled slice. Fitting 2447 px into a browser hides
  exactly the dither and banding these sliders exist to judge.
- The bookmark buttons jump to the moments that matter — near-black at 2:40,
  strobing at 3:51. A look that only works at one of them is not finished.

`look.json` is small and **belongs in git**: it is the artistic decision, and it
is what makes another machine render the same wall.

---

## Status

| | |
|---|---|
| canvas geometry | **solved and verified** — see the five facts below |
| masks | **done** — 31 mattes, annotation text removed, rebuildable |
| reference data | **done** — arc, openings, region rectangles, ID map, SDF |
| background look | **working** — sky + clouds on the wall, thin-film oil in the windows |
| objects | **working, thin** — cubes, pyramids, diamonds, spheres travelling window → door. The choreography is the least developed part. |
| delivery chain | **built and tested** — slice, encode, verify, preview |
| full-resolution pass | **not yet run** |
| projection test on the actual wall | **not yet done** — the one step nobody should skip |

Measured throughput: **31 fps at 2447 × 638** (faster than realtime), and a full
9788 × 2552 background frame costs about **0.14 s** even on an Intel iGPU. The
GPU is not the bottleneck; decoding the noise and writing frames is.

---

## The five facts that matter

**1. The two videos overlap by 1000 px.** They are not two halves.
`SPSW1 = x 0..7200`, `SPSW2 = x 6200..9788`, canvas `9788 × 2552` = the mask
exactly. Verified by 2D cross-correlation.

**2. The overlap is not pre-blended.** Both plates carry full brightness there;
the soft edge is applied downstream. Render one master and slice it —
`make_delivery.ps1` does it in one pass — and never bake a ramp in.

**3. The noise plate is the floor, never the ceiling.** It runs across all twelve
surfaces and is what makes them read as one piece. Composite over it, never
replace it, never out-brighten it, and drive your parameters from its arc.

**4. Only black in the mask is non-projection.** Every other colour is a
projection surface; the colours describe surface *type*. Multiply the comp by
`MASK_08_PROJECTABLE` as the last step.

**5. TouchDesigner is out of the delivery path.** Non-Commercial refuses to
output above 1280 × 1280. `scripts/render_shader.py` runs the same GLSL on a
plain OpenGL 3.3 context at any resolution instead.

---

## Where everything lives

```
_pipeline/
  docs/03_48H_PLAN.md        hour-by-hour, and what to cut if time runs short
  docs/00_TECHNICAL_SPEC.md  every measured fact about the files
  docs/01_PIPELINE.md        how the render and delivery work
  docs/02_PORTABLE_RENDER.md moving to another machine or render node
  docs/04_DECISIONS.md       why things are the way they are, and what would
                             change them - read before overriding anything
  project.json               every constant. the scripts read it; edit it here
  masks/                     31 region mattes, full canvas + per-plate crops
  reference/                 arc, openings, region rectangles, ID map, SDF
  scripts/                   render, check, build, extract, slice, encode, verify
  shaders/                   sky_oil.frag + the PS1 object shaders
shaderRefs/engine/           the artist's original Three.js shaders, the source
                             sky_oil.frag was ported from
```

Start with `_pipeline/docs/03_48H_PLAN.md` if you are working, or
`_pipeline/docs/00_TECHNICAL_SPEC.md` if you are trying to understand the files.

---

## Everything generated is reproducible

Nothing in `masks/` or `reference/` is hand-made. If any of it is lost or looks
wrong, delete it and re-run:

```powershell
python build_masks.py     # all 31 masks, region tables, opening ID map, SDF
python analyse_arc.py     # noise_arc.csv
```

---

## Open with the producer

1. **The C4D toolkit** (`TOOLKIT_3D_PxDL_MAINEXPO_v4.c4d`) — for the real wall
   dimensions and UV layout. The 6 m estimate is too low: the doors are 28.8 % of
   the canvas height, which would make them 1.73 m tall. The canvas is probably
   nearer 10 m. See `docs/00_TECHNICAL_SPEC.md` §6.
2. **Codec preference** — offer ProRes 4444 plus the 16-bit master on the drive.
3. **Handles** — 4770–7229 (±1 s) is the current default.
