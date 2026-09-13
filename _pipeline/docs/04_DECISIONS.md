# Why things are the way they are

Every non-obvious choice in this repo, what forced it, and what would change it.
Read this before overriding something — most of these were made because the
material left no alternative, but a few are judgement calls that could go the
other way.

---

## Geometry and delivery

### Both plates are sliced from one master. Always.
**Because** the 1000 px overlap must be pixel-identical in both plates. Rendering
them separately does not produce that even from identical inputs — and the
supplied plates are the proof: they were encoded independently and **differ from
each other by a mean of 7.1 through their own overlap** (16 frames sampled
across the delivered segment: 0.0 where the plate is black, 12.8 at the busiest;
across the whole loop the worst sample is 25.0). That is the failure mode, and
it is already in the material we were given.
**Changes if:** never. `verify_plates.py` exists to catch it; sliced from one
frame it reports 0.0000 in ProRes 4444.

> Earlier drafts of this file quoted 11.7 as "the" figure. That is the value at
> **frame 6000 specifically**, from the cross-correlation in `00_TECHNICAL_SPEC.md`
> §2 — a single frame, not an average. The difference tracks how bright the
> plate is, so there is no single number; the distribution above is the honest
> answer.

### The overlap ships at full brightness, with no blend ramp.
**Because** the supplied plates do exactly that — column means through all 1000
overlap columns sit at ratio 0.98–1.04 with no ramp. The soft edge is applied
downstream by the media server.
**Changes if:** the producer says their server expects pre-blended content. Ask
before assuming; baking a ramp into a server that also blends would double-darken
the seam.

### Frame numbers follow the ten-minute loop (3270–7529), not 0.
**Because** nobody downstream should have to work out where the segment sits.
**Changes if:** the producer asks for 0-based. Trivial to change.

### Only 1, 2 and 4 are allowed as scale divisors.
**Because** gcd(9788, 2552) = 4. Any other divisor puts the render on a
fractional pixel grid, and the dither then crawls and shimmers — very visible on
a facade and it survives compression badly.
**Changes if:** never, unless the canvas changes.

---

## The look

### The noise plate is the floor, never the ceiling.
**Because** it runs across all twelve surfaces and is what makes them read as one
piece. The artist confirmed it is the permanent base.
**Practically:** always composite over it, never out-brighten it, and drive
parameters from its arc so transitions happen *because* the show transitions.

### The plate shades the sky rather than screen-blending onto it.
**Because** at its peak the plate is near-white. Screen-blending washed all the
colour out of both the sky and the oil — the wall went flat white. Using its luma
as a multiplier keeps every bit of the dither structure while the sky and oil
keep their hue.
**Changes if:** you want the plate to dominate. `--plate-mix 0` removes it
entirely, `1.0` is as strong as the current curve goes.

### `uSkyGain` lifts the base sky but not the clouds.
**Because** the cloud colour already reaches ~1.0; multiplying it too just clips
to white. Only the darker base gradient has headroom.

### The camera is orthographic. No anamorphic perspective.
**Because** the artist confirmed there is no viewer sweet spot — people move
around the hall. Anamorphic geometry only works from the position it was
constructed for and gets progressively wrong everywhere else.
**Consequence:** objects cannot appear to float out into the room. They emerge
from an opening onto the wall plane and travel across it — which reads correctly
from every position.
**Changes if:** the show turns out to have a designed sweet spot after all.

### `noperspective` on the object UVs currently does nothing.
**Because** with an orthographic camera `w` is constant, so there is no
perspective to correct. It is left in deliberately: it costs nothing and starts
working the moment anyone switches to a perspective camera.

### The pillars take the plate's grain but not its banding.
**Because** they stand in the *room*, in front of the wall — so the wall's own
lighting must not print through them. It used to: measured on one frame, the
plate runs at 78–86 across the band the capital sits in, 47–58 down the shaft
and 58–69 again at the base, and multiplying the pillar by that put a bright cap
and a bright plinth around a dark shaft. It read as a shadow lying across the
middle of the pillar.
**Practically:** the plate is high-passed for the pillars — its own local average
subtracted, the frame's mean put back — which keeps every bit of the dither and
drops the architecture's lighting. `--plate-hp 0` restores the old behaviour,
`--plate-blur` sets what counts as local (110 canvas px: bigger than the dither,
smaller than the bands). Measured after: the cap went from +40 % of the shaft to
+18 %, the plinth from +14 % to +2 %, grain unchanged (σ 5.68 → 5.79).
**Same mistake as** the chrome looking transparent, which was `uRoomMix`
sampling the plate per pixel and stamping the wall onto the metal.

### The pillars take the sky's COLOUR as ambient light, not its brightness.
**Because** a pillar standing in a room takes the colour of the air around it,
and `--color` is already keyframed across the whole piece — night blue, dawn
violet, a hot sunrise, cold daylight, a red last light. The stone gets the
sky's **hue only**, normalised by its own luma, so dusk and midday shift it
without either one making it darker or brighter than the other.
**Clamped**, because the track reaches (1.15, 0.26, 0.14) at 3:51 and an
unclamped hue that extreme turns limestone into a traffic cone.
**Measured** across the twelve colour keys, the stone's red/blue ratio runs:

| `--pillar-ambient` | red/blue ratio across the piece |
|---|---|
| 0 | 1.24 flat — bare stone, the same all day |
| **0.45** (default) | **0.72 … 2.28** |
| 0.75 | 0.51 … 3.46 |
| 1.0 | 0.38 … 5.10 — the pillars simply become the sky |

### The plate's grain is mostly OFF on the pillars.
**Because** the pillars are the one surface carrying real shading — the
cylinder, the flutes, the sun crossing them — and at full strength the dither
sits on top of all of it and flattens it. Fading toward the frame's own mean
keeps the pillar breathing with the arc and lets the carving read.
**Measured** on the shaft with the objects out of the way, split into three
bands:

```
                dither   carving    form   carving/dither
--pillar-noise 1.0   10.44    15.67   49.48   1.50
--pillar-noise 0.25   4.03    14.93   43.18   3.70
--pillar-noise 0      3.08    14.93   42.27   4.85
```

The speckle drops 61 % from 1 to 0.25 and the carving does not move. Default is
0.25 rather than 0 so the pillars still belong to the same dithered world as
the rest of the wall; 0 turns the plate off there entirely. The 3.08 that
remains at 0 is the palette quantiser, which is the PSX look and is meant to be
there.

> Two measurement traps on the way to those numbers, both of which produced
> confident nonsense first: a chrome object was sitting over the shaft and
> counted as grain, and the first frequency split was cutting straight through
> the flutes so that fading the plate appeared to do almost nothing.

### The capital and plinth are the same stone as the shaft.
**Because** they were lifted — `mix(col, col*1.12 + stone, 0.6)` — which reads as
gentle in the code and measured 41 % brighter than the shaft at the cap. Since
the trim mattes sit at the top *and* bottom of the pillar, that put a bright band
at each end and left the shaft between them looking shadowed. The cap and plinth
are already wider than the shaft; that silhouette is what says "capital", and it
does not need a brightness step as well.
**Changes if:** `--trim-lift` above 0 brings it back.

### The ripple bends the glass rather than shading it.
**Because** shading a straight grid and bending the grid look completely
different from across a hall, and the point of the effect is that the
projection appears to deform the real window. Three separate weights come off
one wave:

| | what it drives |
|---|---|
| `--ripple` | tips the normal, which recolours the interference |
| `--ripple-warp` | **drags the opening's own coordinates**, so the leaded bars and the chamfers are *drawn* bent |
| `--ripple-shade` | how much harder than the static chamfers the wave swings the sunlight |
| `--ripple-grow` | how fast the disturbed AREA spreads — a drop starts as a point |

**The tilt saturates and the warp does not.** The angle term is clamped at 0.45
and the tilt feeds a `normalize()`, so past about 1.4 the colour stops moving
and folds back on itself — turning `--ripple` up further makes it *worse*, not
bigger. `--ripple-warp` has no such ceiling: 0.075 is a swell, 0.15 is a
funhouse mirror.

**It grows.** The first version switched the whole ring field on at its full
extent the instant the object touched the glass, which is the one thing water
never does. The front now expands, measured in *reaches per lifetime* rather
than px/s so a big shape disturbs a big area just as fast as a small one
disturbs a small one. The front is deliberately slower than the phase, so
crests keep welling up in the middle and dying at the rim.

**Measured** at the one crossing with no other ripple alive near it (frame
3600), on the glass within 600 px of the impact:

```
                strength   pace   reach @0.2s   @1.5s
as it was           2.67   0.89        1348      1277
now                 4.03   0.43         191       481
```

Half again as strong, 2.1x slower, and it *grows* — 191 px to 481 px — where
before it was at full size on the first frame and only ever faded.

> *Pace* is how much the ripple's own contribution changes per frame relative
> to its own size; measuring it against itself is what separates "slower" from
> "smaller". And this is measured **locally**, within 600 px of the impact,
> because a mean over all the glass stopped being a fair comparison the moment
> growth went in: a ripple that starts as a point touches far fewer pixels than
> one that switches on across the whole wall, so the average falls even as the
> thing gets stronger where it is actually happening. An earlier version of this
> file quoted that whole-glass average. It was measuring the wrong thing.

**The plate is not warped.** Only the opening's own coordinates are. The shared
noise stays exactly where it is, for the reason at the top of `main()` in
`sky_oil.frag`.

### Animation locks to 30-frame increments between 3:22 and 3:44.
**Because** every cut the plate makes in that window lands on an exact whole
second — a 1 Hz grid. After 3:51 it breaks the grid and strobes, so go free there.

---

## The look

### The tuned look is applied as argparse DEFAULTS, not a separate profile.
**Because** the delivery must use what was dialled in without anyone having to
remember to pass it. `tune_look.py` writes `_pipeline/look.json`;
`render_shader.py` feeds it to `ap.set_defaults()` before parsing. So
`EXPORT.cmd`, which knows nothing about any of this, renders the tuned look —
and an explicit flag on the command line still wins, because that is what
argparse defaults mean.
**Practically:** `--no-look` ignores the file, `--print-defaults` reports the
built-in values, and a corrupt or unreadable `look.json` prints a warning and
renders with the built-ins. It cannot stop a delivery at two in the morning.
**Changes if:** the piece ever needs more than one look at once. Then it becomes
`--look <file>` and the sliders save presets.

### An animated setting is keys in the same file, not a second system.
**Because** the alternative was a separate "animation" file and a separate code
path to apply it, and then two ways for the delivery to be wrong. A setting in
`look.json` is either a value or `{"ease": …, "keys": [[frame, value], …]}`, and
that is the whole format. Plain values become argparse defaults exactly as
before; keyed ones are evaluated per frame in the render loop — which already
set every look uniform once per frame for its own reasons, so animating them
costs a dictionary lookup.
**Practically:** keys hold at both ends rather than extrapolating, because
beyond them lie the handles, where this surface has to match the other eleven.
Colours and the film range interpolate per component and come back out as the
same comma strings, so nothing downstream can tell an animated colour from a
fixed one.
**Consequence:** a setting that is animated cannot also be given on the command
line — the track would overwrite the flag every frame. The renderer prints a
NOTE naming the clash rather than letting the flag silently do nothing.

### The tuner previews through --look, not through a string of flags.
**Because** no command line can express a keyframe. Writing the page's state to
a preview look file and rendering with `--look` means a preview goes down
exactly the path the delivery goes down, animation included. The preview file is
written next to the frame in `work/`, never over `_pipeline/look.json`: moving a
slider must not change what the delivery would render until Save is pressed.

### The easing maths is written twice, in python and in javascript.
**Because** the page has to draw the curve and show what a slider reads at the
playhead without a three-second round trip for every pixel. The copies must stay
identical, and there is a comment in each saying so. The risk is contained: the
PICTURE always comes back from the real renderer, so a disagreement could only
ever mislead about the delivery, never change it.

### The slider ranges live in tune_look.py; the values live in the renderer.
**Because** two copies of a default drift apart, and the one that would be wrong
is the one you are looking at. `tune_look.py` asks
`render_shader.py --print-defaults` and fills the sliders in from that, so there
is exactly one copy of every number and it is the renderer's. Renaming a setting
makes the tuner say which slider it dropped rather than showing one that
controls nothing.

### The tuner is a local web page, not a desktop window.
**Because** the python on this machine has no tkinter — no tcl/tk was installed
with it — and the render PC cannot `pip install` anything, its DNS being broken.
A browser is on every Windows machine and needs no packages. The server binds to
`127.0.0.1`, so nothing off the machine can reach it, and it needs no internet.
**Consequence:** previews cost a process start each, about 3 s a frame at ÷4
rather than a live 30 fps view. Worth it against rendering 30-second mp4s to
judge a slider, which is what this replaced.

---

## Rendering

### TouchDesigner is not in the delivery path.
**Because** the artist has only a Non-Commercial licence, which refuses to output
above 1280 × 1280. A 9788 × 2552 delivery is impossible with it. Free DaVinci
Resolve has the same problem at 3840 × 2160.
**Instead:** `render_shader.py` runs the same GLSL on a standalone OpenGL 3.3
context via moderngl. No licence, any resolution, and measured at 31 fps at
÷4 — faster than realtime.
**Changes if:** a commercial TD licence appears. The TD-flavoured shader variants
are still in `shaders/` for that case, though they have never been compiled.

### moderngl rather than Unreal, even though UE 5.6 is installed and free.
**Because** the look is fundamentally a fragment shader the artist already wrote,
plus four primitive meshes. Unreal would mean rebuilding all of it in an
unfamiliar tool, and getting the noise plate in as a texture through Media
Framework is fiddly and can drift out of sync over 80 seconds.
**Changes if:** the piece needs skinned characters, physics, or a large asset
library. Then render only the 3D layer in UE's Movie Render Queue as EXR with
alpha and composite it back — do not make UE the spine.

### `uTime` is absolute segment time, not time within the rendered chunk.
**Because** the final render is meant to run in chunks so a crash costs one chunk
rather than the night. Chunk-relative time would restart the cloud animation at
every boundary — invisible while testing a single chunk, obvious in the joined
result.
**Changes if:** never. This is a correctness property, not a preference.

### Design against the mp4s, final render against the ProRes.
**Because** the supplied mp4s are 0.019 bits/pixel with a measured DCT
block-edge ratio of 1.22–1.41 — genuinely preview-grade. Since the plate stays
visible in the render, that compression would ship. But they are also small and
fast, which is what an iteration loop needs.
**Practically:** `project.json` → `source_hq` plus `--hq` on the final pass.

---

## The mask

### The annotation text is removed by nearest-label fill plus edge reconstruction.
**Because** the supplied mask has `PxDL_SW_9788x2552` and three `CLOSED DOOR`
labels burned into it — 1.7 % of the canvas. Used raw it corrupts the wall and
door mattes and inflates six window bounding boxes.
**The first attempt was wrong:** filling each glyph blob with the majority label
on its boundary ring bit glyph-shaped notches out of the window bottoms wherever
the title crossed them. Per-pixel nearest-label fill respects local geometry, and
each opening's flat bottom edge is then rebuilt from its own undamaged columns.
**Verification:** 472 ragged columns → 0 across all 18 upper windows, and
undamaged windows are untouched.

### The authored mask is otherwise left exactly as drawn.
**Because** it is the client's data and reinterpreting it silently is worse than
leaving an oddity in place. Two known oddities are deliberately preserved:
- three narrow light-blue strips flanking the doors — the artist identified these
  as the insides of the cut door frames
- window 9's two leftmost columns stop short (y 294 and 608 instead of 732) —
  this is in the original artwork, not damage

### There is a second mask set, and it only ever MOVES a shape.
**Because** the plate has the same architecture drawn into it, and the two do
not agree — measured per shape, up to 20 px. Now that the plate *drives* the
shaders rather than sitting behind them, a window 12 px out of register spills
its oil field onto the masonry.
**The first attempt was wrong:** it re-grew every boundary onto the plate's own
border lines. Right place, wobbly edge — a boundary grown a pixel at a time
follows every wrinkle in a 0.019 bits/pixel mp4 — and it ate 3 % of the black
area. On a facade that has to line up to the pixel, a wobbly edge in the right
place is worse than a clean edge a few pixels out.
**Instead:** every shape is translated as one rigid piece. Nothing is redrawn,
nothing is deformed, NOMAP is not touched. Verified on pixels: 21 of 29 groups
are exact translations, 8 are extended along a flat canvas edge, 0 are deformed,
and the black area differs from the authored one by 0 px.
**Changes if:** the plate is replaced. Re-run `build_masks.py --align --hq` and
check the same numbers. Full account in `06_MASKS.md`.

### Only black is non-projection.
**Because** the artist confirmed it. The other colours describe surface *type*,
not whether to light them. `MASK_08_PROJECTABLE` is everything but the black, and
multiplying by it as the final step costs nothing and guarantees no light lands
where it should not.

---

## Known assumptions that could still be wrong

- **Physical scale.** Nobody has confirmed the wall dimensions. The door-height
  argument puts the canvas near 10 m tall, but that is inference, not measurement.
  Get the C4D toolkit.
- **Delivery codec.** ProRes 4444 is a recommendation, not a requirement from the
  producer.
- **Handles.** ±1 s is a guess at what the neighbouring segments need.
- **The shaders have never been seen on the wall.** Everything about brightness,
  contrast and dither pitch is judged on a monitor. A facade is not a monitor.
  Do the projection test before committing to a full render.
