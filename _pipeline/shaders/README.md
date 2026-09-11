# Shaders

**These compile and run.** `scripts/render_shader.py` builds them on a plain
OpenGL 3.3 context — no TouchDesigner, no licence, any resolution.

| file | what it is |
|---|---|
| `lib_common.glsl` | **shared.** hash / noise / fbm / fresnel / thinFilmReflectance verbatim from your `PS1_SKY_FRAGMENT`, plus `skyEnv()` — one function for the world, called by the wall AND by the chrome, so the metal reflects the same sky the wall is showing. `#include`d; `render_shader.py` resolves that itself because core GL 3.3 has no include. |
| `sky_oil.frag` | **the background.** 12-step banded sky intact; oil per opening; fake jambs whose lit side follows the camera. |
| `chrome_object.vert/.frag` | **the objects.** Polished metal: no diffuse, fresnel-weighted environment, a dark ground under the sky with a hot horizon line, filmic tonemap. |
| `pillars.frag` | **the two column bays**, as the nearest layer, cylinder-shaded with a highlight that slides as the camera moves. |
| `psx_object.vert` | vertex snapping (`floor(pos * resolution) / resolution`, your own) + affine UVs |
| `psx_object.frag` | flat colour, reduced colour depth, ordered dither |
| `sky_oil.glsl`, `psx_vertex.glsl`, `psx_pixel.glsl`, `oil_clouds.glsl` | TouchDesigner-flavoured variants, kept in case you want to design inside TD's 1280 limit. **Not compiled** — expect to fix a few lines. |

## Tuning `sky_oil.frag`

Every uniform is a command-line flag on `render_shader.py`, so the loop is
edit → render 30 s → watch, about 30 seconds a pass:

```
--sky-gain 1.7      base sky brightness (gaining the clouds too just clips them)
--oil-gain 2.6      oil intensity before the tonemap
--spread 0.95       how much the SKY's view angle varies across the wall
--oil-sweep 0.55    the oil's view-angle swing, per opening. Above ~0.8 the film
                    reaches grazing incidence at the opening edges, fresnel goes
                    to 1 and the windows blow out white - which is exactly the
                    bug this replaced, when the angle came from the pixel's
                    position across the whole 9788 px wall instead.
--film 220,780      thin-film thickness range in nm
--cloud 0.55        coverage
--plate-mix 0.55    how hard the plate's dither shades the sky
--horizon 0.34      where the horizon sits on the wall
--bands 12          sky quantisation — their value
--levels 32         colour depth, 32 = PSX 5-bit
--snap 140          vertex snap on the objects; lower = more wobble
--color r,g,b       theme colour
```

One thing worth knowing: the plate does **not** screen-blend onto the sky. At
peak brightness the plate is near-white and screening washed all the colour out.
Its luma *shades* the sky instead — you keep every bit of the dither structure
and the sky and oil keep their hue.

---

## Running them in TouchDesigner instead

If you want TD's interactivity for design (remember it cannot output above
1280×1280), the `.frag` files need four changes:

1. `uniform sampler2D uPlate/uMasks/uOpenId` → `sTD2DInputs[0..2]`
2. `in vec2 vUv` → `in vec3 vUV` and use `vUV.st`
3. `out vec4 fragColor` → keep, but wrap the write: `fragColor = TDOutputSwizzle(...)`
4. drop the `#version 330` line — TD adds its own

---

## `oil_clouds.glsl` — GLSL TOP (pixel shader)

The main look: an oil film that morphs into clouds, driven by the noise plate.

**Wiring**

```
Movie File In TOP  (noise plate)      -> input 0
mask stack (RGBA, cached)             -> input 1
reference/PxDL_SW_OPENING_ID_*.png    -> input 2     Filter: Nearest, NO mipmaps
reference/PxDL_SW_OPENING_SDF_*.png   -> input 3     Filter: Nearest
                                          |
                                          v
                                      GLSL TOP  -> composite
```

Build the mask stack **once** and cache it (Null TOP, Cook Type: Selective) or
bake it to a file. Do not re-composite the masks every frame.

```
R = PxDL_SW_MASK_01_WALL
G = PxDL_SW_MASK_02_WINDOW
B = PxDL_SW_MASK_04_DOOR
A = PxDL_SW_MASK_08_PROJECTABLE
```

Use a Reorder TOP to pack four greyscale TOPs into one RGBA.

The **ID map must be point-sampled with no mipmaps**. Its red channel is an
integer index; any filtering interpolates between window 6 and window 7 and you
get window 6.5, which does not exist.

**Uniforms** — GLSL TOP *Vectors* page:

| name | type | start | notes |
|---|---|---|---|
| `uTime` | float | seconds 0-80 | into your segment, not the loop |
| `uMorph` | float | 0 -> 1 | **drive from `reference/noise_arc.csv` `mean_norm`** |
| `uArc` | float | 0 -> 1 | same source; keeps your wall level with the hall |
| `uFlow` | float | 0.05 | overall speed |
| `uEmerge` | float | 0 -> 1 | how far the emergence wave has grown out of the windows |
| `uWindowSpread` | float | 6 | seconds of delay spread across the 27 openings |
| `uLevels` | float | 32 | colour steps per channel; 0 disables |
| `uGrid` | float | 2 | dither cell in px; match your render scale |

To drive `uMorph` / `uArc` from the CSV: File In DAT -> DAT to CHOP -> Lookup
CHOP indexed by frame. More predictable than sampling the live video, and it
survives you swapping the video input.

### Per-window timing

The shader derives a stable per-opening delay from the ID map:

```glsl
float idN    = (openIdx - 1.0) / 26.0;           // 0..1 across the 27 openings
float jitter = hash21(vec2(openIdx, 17.0));      // stable per-opening random
float delay  = (idN * 0.6 + jitter * 0.4) * uWindowSpread;
```

**Use the same expression in your 3D layer** so the geometry emerging from
window 7 is in step with the glow around window 7. `openings.json` gives you the
index, kind, row and rectangle for all 27 - left to right, upper row first, then
the lower row, then the three doors.

---

## `psx_vertex.glsl` + `psx_pixel.glsl` — GLSL MAT

For the low-poly models emerging from the windows.

**Wiring**

```
Geometry COMP ──► GLSL MAT (these two files)
                     └─ sTD2DInputs[0] = albedo TOP
                          Filter: Nearest · Mip Map Filter: None · Anisotropic: Off
```

That sampler state is **not** optional — point sampling is half the look, and
the shader cannot set it for you.

**Uniforms** — vertex shader:

| name | type | start value | notes |
|---|---|---|---|
| `uSnap` | float | 200 | snap grid in virtual pixels; lower = more wobble. 0 = off |
| `uLightDir` | vec3 | `0.3, 0.8, 0.5` | world direction **to** the light |
| `uLightCol` | vec3 | `1, 0.95, 0.85` | |
| `uAmbient` | vec3 | `0.25, 0.28, 0.35` | keep lifted — PSX had no bounce light |
| `uEmissive` | float | 0 → 0.6 | drive from the plate so models pulse with the show |

Pixel shader:

| name | type | start value | notes |
|---|---|---|---|
| `uLevels` | float | 32 | 5 bits per channel = PSX |
| `uGrid` | float | 4 | rendering 3D at ÷4 and upscaling ×4 → use 4 |
| `uFogColor` | vec3 | `0.02, 0.02, 0.04` | match your background |
| `uFogNear` | float | 8 | world units |
| `uFogFar` | float | 40 | world units |
| `uCutout` | float | 0 | alpha-test threshold; 0 disables |

**Render the 3D at 2447x638 (÷4)** and nearest-upscale ×4 into the full canvas.
That is authentic PSX *and* it is what keeps you inside 4 GB of VRAM.

---

## Matching the dither to the plate

The supplied plate has its own fine dither at roughly 2–3 px. If your Bayer cell
lands on a different pitch the two patterns will beat against each other and
crawl — very visible on a facade, and it survives compression badly.

Keep `uGrid` a whole multiple of your render scale:

| layer | render at | upscale | `uGrid` |
|---|---|---|---|
| shader | 4894x1276 (÷2) | ×2 | 2 |
| 3D | 2447x638 (÷4) | ×4 | 4 |
| shader at full res | 9788x2552 | ×1 | 1 or 2 |

Check it by rendering a flat mid-grey frame and looking at it at 100 %. The
pattern should be perfectly static from frame to frame.
