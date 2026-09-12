# Phoenix de Lumière — SW wall — pipeline

Read `00_TECHNICAL_SPEC.md` first. This is the *how*.

---

## 0. Can this be done? Yes.

Nothing about the brief is unreasonable. The canvas is 3× a 4K frame, the
segment is 80 seconds, and the aesthetic you want — PSX — is the one aesthetic
that gets *cheaper* the more committed to it you are. The two things that could
actually stop you are administrative, not creative:

1. ~~The TouchDesigner licence.~~ **Settled: you do not have one, so TD is out
   for rendering.** `scripts/render_shader.py` replaces it — same GLSL, plain
   OpenGL 3.3, any resolution, no licence. See §2.
2. **Storage.** You need roughly 150 GB of fast scratch. A 1–2 TB external NVMe
   solves it.

Everything else is a matter of working in the right order.

---

## 1. The one rule: the noise is the floor, never the ceiling

The plate runs across all twelve surfaces and is what makes the twelve read as
one piece. So:

- **Your render always contains the plate.** Composite over it; never replace it.
- **Never brighten past it.** Your wall sitting brighter than the building
  around it is the single most likely way to look wrong in the room.
- **Drive your parameters from it.** Sample its luma and feed the arc into your
  shader. Then your transitions happen *because* the show is transitioning.

| drive this | with this |
|---|---|
| oil-film thickness / iridescence hue | plate luma, blurred |
| cloud density and coverage | plate luma, high-passed |
| displacement / parallax depth | plate luma |
| emission of the 3D objects | plate luma sampled at each object's window |
| **oil ↔ cloud morph** | **the arc: `reference/noise_arc.csv` `mean_norm`** |

That last row is the strongest move available to you. The plate ramps 0.088 →
0.640 across your 80 seconds. Map that to the morph and the change lands exactly
when the whole hall brightens.

Lock animation to **30-frame (1 s)** increments from 3:22 to 3:44 — that is the
plate's own cut grid — then go free after 3:51 when it starts strobing.

⚠ Because the plate stays in your render, its compression becomes your
compression. **Ask the producer for the noise master** (see spec §3). At
0.019 bits/pixel the supplied mp4 is preview-grade.

---

## 2. Toolchain — no licence anywhere in it

TouchDesigner Non-Commercial refuses to output above 1280×1280, so it cannot
produce a 9788×2552 delivery. Free DaVinci Resolve caps at 3840×2160. Rather
than buy either under time pressure, the render runs on a plain OpenGL 3.3
context:

```
python -m pip install moderngl
python render_shader.py --div 4 --start 5700 --count 900 --mp4 --preview-width 1224
```

`scripts/render_shader.py` does the whole frame:

- **background** — `shaders/sky_oil.frag`, your own `PS1_SKY_FRAGMENT` with its
  banded sky and thin-film oil, the `useLighting` cross-fade now driven by the
  window mask
- **objects** — low-poly cubes, pyramids, diamonds and spheres, flat-shaded,
  vertex-snapped with your own `floor(pos * resolution) / resolution`
- **composite** — objects screen-blended over the noise while travelling, then
  sinking under it as they leave through a door
- **output** — PNG sequence for delivery, or a small MP4 for review

Measured on this machine: **31 fps at 2447×638** (faster than realtime), and a
full 9788×2552 background frame costs about **0.14 s** even on the Intel iGPU.
The GPU is not the bottleneck; decoding the noise and writing frames is.

You can still use TouchDesigner to *design* within its 1280 limit if you prefer
its interactivity — the shader is the same file, see `shaders/README.md`. But
nothing in the delivery path needs it.

If you later need skinned characters or physics, Unreal 5.6 is installed and its
Movie Render Queue handles arbitrary resolutions with tiling. Render only the 3D
layer there as EXR with alpha and composite it back.

## 3. The render strategy that fits on 4 GB

The PSX aesthetic *wants* the thing that also makes this renderable: **low
internal resolution, nearest-neighbour upscale.**

```
3D / PSX layer        render at  2447 x 638   (÷4)   → nearest upscale ×4
shader / cloud layer  render at  4894 x 1276  (÷2)   → nearest or linear ×2
final composite       full        9788 x 2552
```

÷2 and ÷4 are the only clean divisors. Keep the comp chain short, stay 8-bit
fixed where you can, reserve 16-bit float for the shader pass. A *Render TOP* at
full canvas with MSAA is what will blow the budget — which is exactly why the 3D
goes at ÷4.

At ÷4 each rendered pixel becomes a 4×4 block on the wall. Against the plate's
fine 2–3 px dither that reads as a deliberate contrast between a crisp etched
ground and chunky low-fi geometry. Decide it consciously; if it is too coarse,
move the 3D to ÷2.

**Render non-realtime.** In TD set Movie File Out / Render to non-realtime so
every frame is fully computed. Never trust a realtime capture for a deliverable.

---

## 4. Objects coming through the windows, with no sweet spot

You said there is no sweet spot — people are everywhere. That rules out
anamorphic perspective, and it is genuinely good news: it removes a whole class
of problems.

**Use an orthographic camera aligned to the facade.** The canvas is already a
flattened elevation, so ortho is the honest projection and it reads correctly
from every position in the hall.

What that means for "coming out of and going through the windows": you cannot
make something appear to float in the room, because there is no single viewpoint
to construct the illusion for. But you *can* do the thing that actually works in
a mapped room, which is the window as a **threshold**:

- An object rises out of the window opening and **crawls onto the wall surface**,
  travelling across the facade, then slips into another window.
- Objects are **silhouettes inside the window** (a space behind the wall), then
  **solid on the wall** once they emerge. The change of state sells the passage.
- Keep any actual depth **shallow** — a metre or so of parallax at most. Read as
  relief, not as a room.
- Scale objects to the window. At ~255 px/m an upper window is ~1.3 × 2.4 m and
  a lower window ~2.4 × 4.5 m. A figure crawling out of a lower window is
  human-scale; the same figure out of an upper window reads as a doll.

Two assets are built for exactly this:

**`reference/PxDL_SW_OPENING_ID_9788x2552.png`** — for each of the 27 openings:
`R` = opening index 1–27, `G`/`B` = normalised UV *inside* that opening. A
shader can give every window its own timing offset and its own local space —
so window 7 can be two seconds behind window 6 with no extra geometry.

**`reference/PxDL_SW_OPENING_SDF_9788x2552.png`** — distance from the nearest
opening edge, 0 = inside an opening, 255 = 512 px away or more. Drive glow
falloff, emergence wipes that grow outward from the windows, and depth cues.

`reference/openings.json` lists all 27 with kind (WINDOW/DOOR), row
(upper/lower/ground), rectangle and centre.

---

## 5. The PSX look

Six things, in order of how much they matter:

1. **Low internal resolution, nearest upscale.** Already in the render plan.
2. **Vertex snapping** — quantise clip-space XY to a coarse grid in the vertex
   shader. The characteristic PSX wobble.
3. **Affine texture mapping** — no perspective correction, so textures swim on
   large polygons. In GLSL: mark the UV varying `noperspective`.
4. **Nearest texture filtering**, no mipmaps. This is a sampler state, not a
   shader setting.
5. **Vertex (Gouraud) lighting only.** No per-pixel lighting, no shadows.
6. **Reduced colour depth with ordered dither** — ~5 bits per channel with a
   Bayer matrix. Match the Bayer cell to your pixel grid so it locks to the
   plate's dither instead of beating against it.

`shaders/psx_vertex.glsl` and `shaders/psx_pixel.glsl` implement 2–6. Fog is a
cheap, period-correct depth cue and keeps geometry from fighting the plate.

---

## 6. Delivery

You deliver **two videos**, matching the two plates you were given. The producer
stitches.

Render **one 9788×2552 frame**, then slice it. Never render the plates
independently — the 1000 px overlap must be identical in both.

Since the renderer exists this needs no intermediate sequence at all. Each frame
is sliced in memory and written straight into both encoders, so the overlap is
identical **by construction** rather than by process:

```
render_shader.py --layout plates
        │   one 9788x2552 frame in GPU memory
        ├──── x    0 .. 7200  ──→  PxDL_SW_SPSW1_03270-07529_MASK-LAYER.mov   7200 x 2552
        └──── x 6200 .. 9788  ──→  PxDL_SW_SPSW2_03270-07529_MASK-LAYER.mov   3588 x 2552
```

`--layout canvas` writes one stitched 9788×2552 file instead, and `--layout
both` writes all three from the same render. **`export_delivery.ps1` drives all
of this** — see `05_DELIVERY.md`. The older `make_delivery.ps1` /
`master_to_plates.ps1` path still works if you ever have a master sequence on
disk from somewhere else.

### Codec — since it is your choice

| | use when |
|---|---|
| **ProRes 4444** (default) | the safe "best quality" answer. 12-bit 4:4:4, every tool reads it, ~43 GB for the segment. |
| ProRes 422 HQ | if 43 GB is a problem. 10-bit 4:2:2, about half. |
| **16-bit PNG sequence** | if the producer accepts sequences. Truly lossless, zero codec ambiguity. ~85 GB. |
| FFV1 / MKV | lossless in a single file, but poor NLE support. Works with the ffmpeg you already have. |
| H.264 CRF 12 | only if they insist on mp4. |

Ask the producer which they want, but **offer ProRes 4444 plus the 16-bit master
on the drive**. That covers every downstream decision they might make.

**Tag the colour explicitly as Rec.709.** The two source plates disagree about
this (one tagged bt709, one untagged) and an untagged delivery is exactly how a
brightness step at the seam gets introduced. `make_delivery.ps1` tags it for you.

**Use the loop's own frame numbering** (3270–7529) so nobody has to guess where
the segment sits in the ten minutes. The filenames carry it, and so does
`DELIVERY_NOTES.txt`.

### Always verify before sending

```powershell
python verify_plates.py --a <SPSW1> --b <SPSW2> --div 1 --tol 2
```

It checks resolution, frame count, and that the overlap holds. `export_delivery.ps1`
runs it automatically with the right tolerance for the codec and refuses to
report success if it fails.

Tested both ways: plates sliced from one frame report **0.0000** in ProRes 4444;
the two *supplied* source plates, encoded independently, differ by a mean of
**7.1** through their own overlap (0.0 on black frames, 12.8 at the busiest), and
genuinely mismatched plates read higher still. The per-codec tolerances and what
they mean are in `05_DELIVERY.md`.

---

## 7. Order of work

1. **Check the TouchDesigner licence.** Run `check_environment.ps1`.
2. Get a 1–2 TB external NVMe, point the output roots at it (see
   `02_PORTABLE_RENDER.md`).
3. Ask the producer for: the C4D toolkit, the noise master, and their codec
   preference.
4. Extract your working plate: `extract_segment.ps1 -Div 4`.
5. Build the comp in TD at **÷4 end to end**. Everything — shader, masks, 3D,
   composite. Iterate fast at this size.
6. Get the oil↔cloud morph driven by the arc and locked to the cut grid. **Get
   this right before you touch the 3D.** It is the backbone; the objects are
   decoration on top of it.
7. Add the 3D layer. One object per opening from `openings.json`, timing offset
   per window from the ID map. Keep depth shallow.
8. Switch the composite to full 9788×2552, keep the 3D at ÷4. Render test frames
   at 2:50 (dark), 3:30 (ramp), 3:40 (peak), 3:56 (strobe). Check VRAM headroom
   and per-frame time.
9. Render 5 s, slice, verify, and **send it to the producer for a projection
   test** before committing to the full 80 s.
10. Render 2460 frames, encode, verify, deliver.

Step 9 is the one people skip and regret. A facade always looks different from
the monitor.
