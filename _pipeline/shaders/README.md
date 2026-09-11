# Shader starting points

⚠ **These have not been compiled.** They are written to TouchDesigner's 2023
GLSL conventions but there is no TD on this machine to build them against.
Expect to fix a few lines on first load — check the GLSL TOP / MAT info popup
for the error, it names the line. Treat them as a structured starting point,
not drop-in code.

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
