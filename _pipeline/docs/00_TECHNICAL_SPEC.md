# Phoenix de Lumière — SW wall — measured technical spec

Everything below was measured from the supplied files, not assumed.
Sources: `RawNoise/PxDL_SW_SPSW1.mp4`, `RawNoise/PxDL_SW_SPSW2.mp4`,
`RawNoise/PxDL_SW_9788x2552 (0-00-00-00).png`, `RawNoise/3d room mask.png`.

---

## 1. The venue

From the Cinema 4D screenshot (`TOOLKIT_3D_PxDL_MAINEXPO_v4.c4d`): this is the
**interior of a large exhibition hall**, not a building exterior. The `UV Floor`
object measures **6244.80 × 3831.34 cm = 62.45 × 38.31 m**.

The project has **12 mapped surfaces**, each with its own canvas:

```
CubeEXT   CubeINT   EW   Floor   InfinityEXT   InfinityFloor
InfinityINT   NW   SelfieRoom   SmallMuseum   SW   WW
```

Other canvases visible in the screenshot: `..T_5708x1520` (SOUTH) and
`..XT_6008x1520` (NORTH FACING) on the central cube.

**You own `SW`.** The noise plate runs across all twelve and is what ties the
piece together.

> **Get the C4D toolkit from the producer.** It is not on this machine. With it
> you get the exact wall dimensions, the real UV layout, and the ability to
> build your 3D in true venue space. Without it you are guessing at scale.

---

## 2. Canvas geometry — the single most important fact

The two noise videos are **not** two halves of the wall. They are two projector
plates that **overlap by exactly 1000 px**.

```
full wall canvas              9788 x 2552          (= the mask, exactly)
  SPSW1 (left plate)     x =    0 .. 7200          7200 x 2552
  SPSW2 (right plate)    x = 6200 .. 9788          3588 x 2552
  overlap / soft edge    x = 6200 .. 7200          1000 px wide
```

Verified by 2D cross-correlation of frame 6000 of both plates: best alignment at
**dx = 6200, dy = 0** (mean abs difference 11.7, against 23–29 at ±2 px), and
6200 + 3588 = 9788 = the mask width exactly.

### The overlap is NOT pre-blended
Column means through the overlap are identical in both plates (ratio 0.98–1.04
across all 1000 columns, no ramp). The soft edge is applied downstream.

**→ Your delivered plates must also carry full brightness through the overlap,
with identical content in both.** Do not bake a ramp in. Render one 9788×2552
master and slice it — that is the only way to guarantee it.

---

## 3. Video format

| | SPSW1 | SPSW2 |
|---|---|---|
| resolution | 7200 × 2552 | 3588 × 2552 |
| codec | H.264 Main, yuv420p | H.264 Main, yuv420p |
| frame rate | 30.000 fps (constant) | 30.000 fps (constant) |
| duration | 600.021 s | 600.021 s |
| frames | 18000 | 18000 |
| bitrate | ~10.3 Mbit/s | ~10.3 Mbit/s |
| colour tags | **bt709, TV/limited range** | **none (untagged)** |
| audio | AAC 48 kHz stereo | AAC 48 kHz stereo |

⚠ **The two plates are tagged inconsistently.** SPSW1 declares bt709 / limited
range; SPSW2 declares nothing. FFmpeg decodes both identically, but Resolve,
After Effects or Unreal's Media Framework may apply a different matrix or level
expansion to the untagged file and give you a **visible brightness step at the
seam**. Check the overlap after import. `scripts/verify_plates.py` measures it.

### ⚠ The noise plate is heavily compressed

```
10.3 Mbit/s over 7200 x 2552 at 30 fps  =  0.019 bits per pixel
DCT block-edge / in-block step ratio    =  1.22 to 1.41   (1.0 = no blocking)
```

That is preview-grade. The fine dither the whole piece depends on is being
mangled by the encoder.

**This matters because the noise stays in your render.** If your content sits on
top of the plate, you are re-encoding an already badly compressed image and
adding a second generation of loss.

**→ Ask the producer for the noise master** — ProRes, DNxHR, DPX or EXR, or at
minimum a much higher-bitrate encode. This is the weakest link in the whole
chain and it costs them nothing to hand over.

---

## 4. Your segment

```
in   2:40.000  = 160.000 s = frame 4800
out  4:00.000  = 240.000 s = frame 7200
length                80.000 s = 2400 frames (4800 .. 7199 inclusive)
```

Handles are flexible, so render **4770 .. 7229 (2460 frames, ±1 s)** and let the
producer trim. That is the default in `project.json`.

### Luminance arc (mean luma, 0–1, measured per frame)

| time | mean | character |
|---|---|---|
| 2:40 – 3:10 | 0.07 → 0.15 | near-black, sparse dithered fragments, very low motion |
| 3:10 – 3:32 | 0.25 → 0.69 | steady brightening, the image fills in |
| 3:32 – 3:44 | 0.53 – 0.69 | peak brightness, high contrast |
| 3:44 | — | hard transition, drops back to ~0.19 |
| 3:52 – 4:00 | 0.30 – 0.77 | erratic, strobing, the hardest cuts of the segment |

Min 0.065 @ 2:55.9 · Max 0.766 @ 3:58.4 · ends at 0.64 with a full 0–1 range.
Per-frame data: `reference/noise_arc.csv` (2460 rows, regenerate with
`scripts/analyse_arc.py`).

**You start from darkness and hand off bright and busy.** That is your
dramaturgical contract with the artists either side of you.

### Cut grid
Every detected cut between **3:22 and 3:44 lands on an exact whole second**
(3:22, 3:24, 3:29, 3:30, 3:31, 3:34, 3:35, 3:36, 3:37, 3:43, 3:44) — a
**1 Hz / 60 BPM grid, 30 frames**. From 3:51 it breaks the grid and strobes
freely; strongest hits at 3:55.03, 3:55.57, 3:56.93, 3:57.47, 3:57.63, 3:57.87,
3:58.43, 3:58.77, 3:59.90.

**→ Lock your animation to 30-frame (1 s) or 15-frame (0.5 s) increments through
the build, then go free after 3:51.**

### Texture
Fine dither / halftone at roughly 2–3 px at full resolution, continuous-tone
underneath (172 distinct values in a 256×256 patch — not 1-bit posterised).
Vertical line structure inside the window arches. The architecture is already
cut into the plate: the windows and doors read as shapes in it.

---

## 5. The mask

9788 × 2552, colour-coded by **surface type**.

| colour | RGB | region | coverage | projected? |
|---|---|---|---|---|
| green | 21,145,52 | flat wall | 46.2 % | yes |
| light blue | 33,149,221 | windows | 28.5 % | yes |
| dark blue | 21,109,164 | door frame insides (the doors are cut openings) | 5.4 % | yes |
| magenta | 221,33,168 | base / ground plane, bottom band | 7.1 % | yes |
| purple | 177,33,221 | two column / bay bodies | 4.2 % | yes |
| orange | 255,174,25 | caps and plinths of those columns | 1.7 % | yes |
| **black** | 0,0,0 | **the only non-projected area** | 6.9 % | **no** |

**Every colour except black is a projection surface.** The colours tell you the
surface *type* and orientation, not whether to light it. Use
`MASK_08_PROJECTABLE` as your final multiply — it is everything but the black.

Two solid black blocks: **x 0–823, y 831–2552** (left) and **x 9608–9788,
y 850–2552** (right). Keep them **black, not transparent**, in the delivery.

⚠ **The supplied mask has annotation text burned into it** — the title
`PxDL_SW_9788x2552` on the wall and `CLOSED DOOR` inside each door, 426 k pixels
or 1.7 % of the canvas. Used raw it corrupts the wall and door mattes and
inflates six window bounding boxes. Cleaned mattes are in `_pipeline/masks/`,
rebuildable with `scripts/build_masks.py`.

### Architecture — 27 openings

| # | kind | count | size | note |
|---|---|---|---|---|
| 1–18 | upper windows | 18, in 9 pairs | ~332 × 620 | six of them (pairs at x 1382/1794, 4738/5104, 7930/8289) are ~334 × 730 and run off the top edge — the projecting bays |
| 19–24 | lower windows | 6 | ~600 × 1148 | x = 2618, 3760, 5838, 6936, 9038, plus a 51 px slice at x 823 clipped by the black block |
| 25–27 | doors | 3 | ~556–754 × 740 | x = 1481, 4699, 8015 |

Plus 2 columns (~477 × 1170 at x 3266 and 6427) with an orange cap above and an
orange plinth below.

Exact rectangles: `reference/facade_regions.json` / `.csv` and
`reference/openings.json`.

---

## 6. Physical scale — your 6 m estimate is too low

The canvas is a flattened elevation, so scale is a single multiplier. The
**doors settle it**: they are 735 px tall, which is 28.8 % of the canvas height.

| if canvas height is | px per metre | canvas width | door height | lower window |
|---|---|---|---|---|
| 6 m | 425 | 23.0 m | **1.73 m** ✗ shorter than a person |
| 8 m | 319 | 30.7 m | 2.30 m | 3.60 m |
| **10 m** | **255** | **38.3 m** | **2.88 m** | **4.50 m** |
| 12 m | 213 | 46.0 m | 3.46 m | 5.40 m |
| 16.3 m | 157 | 62.5 m | 4.69 m | 7.33 m |

A 6 m canvas would make the doors 1.73 m tall, so it is wrong. The most likely
answer is **≈10 m tall × ≈38 m wide (~255 px/m)**, which matches the 38.31 m
side of the hall floor and gives sensible 2.9 m doors and 4.5 m arched windows.

Note the canvas cuts through the **top** of the upper windows, so the canvas is
not the full wall height — it is the projector coverage area.

**Confirm it from the C4D file**: select the SW wall geometry and read its Size.
Ten seconds of work, and it removes all of this guesswork.

---

## 7. Hardware and licences

```
GPU     NVIDIA RTX A2000 Laptop, 4 GB VRAM   (+ Intel UHD iGPU)
CPU     i7-11850H, 8 cores / 16 threads
RAM     32 GB
Disk    C: ~41 GB free
```

Canvas = **9788 × 2552 = 25.0 Mpx = 3.01× a 4K UHD frame**.
One RGBA16F buffer at full canvas = **191 MB**.

`scripts/check_environment.ps1` checks all of this on any machine, including a
new render PC.

| tool | version | note |
|---|---|---|
| TouchDesigner | 2023.12230 | ⚠ **Non-Commercial only — caps output at 1280×1280, so it cannot render this canvas.** Confirmed with the artist; TD is out of the delivery path. `scripts/render_shader.py` replaces it. |
| Unreal Engine | 5.6 | Movie Render Queue handles arbitrary resolutions and tiled rendering |
| DaVinci Resolve | 19.0, reports as non-Studio | ⚠ free Resolve caps output at 3840×2160. Not needed — nothing in the pipeline requires it. |
| Cinema 4D | 2026, on another machine | title bar shows a **Non-Commercial / Educational** licence — not licensed for paid commissions |
| Adobe | Photoshop / Premiere / Media Encoder 2026 | no After Effects |
| Python | 3.11 + numpy | |
| ffmpeg | TouchDesigner's build | decodes everything; **can only encode png / dpx / ffv1 / mjpeg / mpeg4** — no ProRes, DNxHR or H.264. Get a full build from gyan.dev or BtbN. |

Neither licence cap blocks anything now: the render runs on a plain OpenGL 3.3
context via `render_shader.py`, and the slicing, encoding and verification run on
ffmpeg, Python and numpy. There is no licensed software in the delivery path.

Useful integer divisors: gcd(9788, 2552) = 4, so only **÷2 = 4894 × 1276** and
**÷4 = 2447 × 638** stay on whole pixels in both axes. Anything else puts the
dither on a fractional grid and it will crawl.
