# 48 hours

One rule above all others: **work at ÷4 (2447×638) until the creative is locked.**
Full resolution is a render problem, not a design problem. You can make every
creative decision at a quarter scale and nothing you learn there will be wrong.

Second rule: **get one complete pass of all 80 seconds in front of your eyes by
hour 7.** Rough is fine. A finished 20 seconds is worth less than a rough 80.

---

## The schedule

### H0–2 · Unblock
- [ ] `python -m pip install moderngl` — this is what replaces TouchDesigner.
      TD Non-Commercial cannot output above 1280×1280 and you do not have a
      commercial licence, so it is out of the delivery path entirely.
- [ ] `check_environment.ps1`
- [ ] External NVMe plugged in, output roots pointed at it.
- [ ] Full ffmpeg installed (gyan.dev / BtbN) → `$env:PXDL_FFMPEG`. Without it
      you have no ProRes and your previews are ~7× larger than they need to be.
- [ ] Put the ProRes master paths into `project.json` → `source_hq`.
- [ ] Render 30 s and watch it:
      `python render_shader.py --div 4 --start 5700 --count 900 --mp4 --preview-width 1224`

### H2–6 · The sky and the oil
`shaders/sky_oil.frag` already runs. This is your own `PS1_SKY_FRAGMENT` — the
12-step banded sky and the thin-film oil unchanged, with the `useLighting` mix
that cross-faded them now driven by the window mask.

Tune it with flags, re-render, watch. Each 30-second pass takes about 30 seconds.

```
--sky-gain 1.7      base sky brightness
--oil-gain 2.6      oil intensity before tonemapping
--spread 0.95       how much the oil bands across the width
--cloud 0.55        cloud coverage
--plate-mix 0.55    how hard the plate's dither shades the sky
--horizon 0.34      where the horizon sits on the wall
--bands 12          sky quantisation (their value)
--levels 32         colour depth (32 = PSX)
--color r,g,b       theme colour
```

- [ ] Sky + clouds on the wall, oil in the windows.
- [ ] Check `--plate-mix` — you must still see the plate's dither through it.

**Do not start the 3D until this reads well.** It is the whole 80 seconds; the
objects are events inside it.

### H6–7 · First full pass — non-negotiable
```powershell
# render all 2460 frames at ÷4 out of TouchDesigner, then:
.\render_test.ps1 -In "E:\PxDL\render\test\out.%05d.png" -Width 1632 -Compare
```
Watch it twice. Note where it sags. At ÷4 this render is minutes, not hours.

### H7–16 · The objects
Spheres, cubes, pyramids, diamonds — already rendering, flat-shaded and
vertex-snapped, in through a window and out through a door. Tune with
`--objects N`, `--seed N`, `--snap N` (lower = more wobble).

The motion lives in `build_objects()` and `object_state()` in
`render_shader.py` — paths, timing, scale, fade and the over/under cross-fade
are all there in about 40 lines. That is where you art-direct them.

```
doors (exit points)    centre x = 1758, 5076, 8332   at y ≈ 2184
windows (entries)      27 of them in reference/openings.json
```

- [ ] One object type per row to start. Upper windows → small, lower → large.
- [ ] Scale to the opening. At ~255 px/m an upper window is ~1.3 × 2.4 m, a
      lower one ~2.4 × 4.5 m.
- [ ] **Over and under the plate.** Screen-blend while travelling so the object
      is clearly readable, then cross-fade to multiply as it nears a door and
      sinks behind the noise. That contrast is the immersion.
- [ ] Fade and scale down on exit — reaching ~0 opacity and ~0.3 scale by the
      time it clears the door.
- [ ] Per-window timing from the ID map so they do not all move together.

**Timebox this to hour 16.** If the objects are not working by then, ship the
sky and oil with three or four hero objects instead of twenty-seven.

### H16–20 · Second full pass, then refine
Another complete ÷4 render. Fix only what the pass exposes.

### H20–24 · Lock
Stop adding. Check against the plate's own structure:
- 2:40–3:10 near-black — stay dark, let the plate lead.
- 3:22–3:44 cuts land on **whole seconds**. Put your beats there.
- 3:51–4:00 strobes hardest. Your climax.
- You hand off bright and busy at 4:00.

### H24–30 · Full-resolution reality check
- [ ] Render **10 frames** at 9788×2552: 4830, 5400, 6000, 6300, 6600, 6900,
      7050, 7100, 7150, 7199.
- [ ] Time one frame. Multiply by 2460. **That number decides everything below.**
- [ ] Watch VRAM. If it will not fit, drop the shader pass to ÷2 and keep the
      composite at full — you will not see the difference on a wall.

### H30–40 · The final render
```powershell
python render_shader.py --div 1 --png --hq
```
- [ ] `--hq` switches the noise to the **ProRes masters**, not the mp4.
- [ ] Render to a **16-bit PNG sequence**, never straight to a movie. A sequence
      survives a crash; a movie does not.
- [ ] Render in **four chunks** (4770–5399, 5400–6099, 6100–6799, 6800–7229).
      If one dies you re-run one chunk, not the night.
- [ ] Check the folder count after each chunk.

### H40–44 · Deliver
```powershell
.\make_delivery.ps1 -MasterPattern "E:\PxDL\render\master\PxDL_SW_master.%05d.png"
python verify_plates.py --a "...SPSW1_4770-7229.mov" --b "...SPSW2_4770-7229.mov" --tol 2
.\render_test.ps1 -In "E:\PxDL\deliver" -Width 1632
```
Watch the preview end to end one last time. Then send.

### H44–48 · Buffer
Something will go wrong. This is where it goes wrong.

---

## If you fall behind

Cut in this order:

1. **Object variety** — one shape, repeated, is a style. Three half-finished
   shapes is a mess.
2. **The number of active windows** — six good ones beats twenty-seven weak.
3. **Handles** — drop to ±0 and deliver 4800–7199 exactly. Tell the producer.
4. **Full resolution** — render at ÷2 (4894×1276) and upscale ×2 with
   nearest-neighbour on the way out. On a wall this size, with a PSX look and
   the plate's own dither on top, almost nobody will be able to tell. It halves
   your render time. **This is a legitimate escape hatch, not a failure.**

Do **not** cut: the projectable mask multiply, the overlap check, or watching a
full pass before you deliver.

---

## Render-time arithmetic

At 9788×2552 you are rendering 25 Mpx per frame, 2460 times.

Measured: the background alone is **0.14 s per full frame** on the Intel iGPU,
and at ÷4 the whole thing runs at **31 fps**. Expect the full-resolution pass to
be limited by decoding the ProRes and writing 25 Mpx PNGs, not by the GPU —
budget 1–3 hours, not a night. Measure one chunk at hour 24 and confirm.

---

## Commands you will actually use

```powershell
cd C:\Projekte\PhoenixDeLumiere\_pipeline\scripts

# render 30 s and watch it
python render_shader.py --div 4 --start 5700 --count 900 --mp4 --preview-width 1224

# the whole segment, full resolution, from the ProRes masters
python render_shader.py --div 1 --png --hq

# preview whatever you just rendered, with frame numbers burnt in
.\render_test.ps1 -In "E:\PxDL\render\test\out.%05d.png" -Width 1632

# your render on top, the raw noise underneath - are you sitting ON it?
.\render_test.ps1 -In "E:\PxDL\render\test\out.%05d.png" -Compare

# the source noise alone, for reference
.\render_test.ps1 -Source -Width 1632

# a folder with SPSW1\ and SPSW2\ - stitches automatically
.\render_test.ps1 -In "E:\PxDL\deliver"
```

`-Width 1224` is the fastest. `-Count 300` previews just the first 10 seconds.
