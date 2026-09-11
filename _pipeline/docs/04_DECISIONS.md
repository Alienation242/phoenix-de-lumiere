# Why things are the way they are

Every non-obvious choice in this repo, what forced it, and what would change it.
Read this before overriding something — most of these were made because the
material left no alternative, but a few are judgement calls that could go the
other way.

---

## Geometry and delivery

### Both plates are sliced from one master. Always.
**Because** the 1000 px overlap must be pixel-identical in both plates. Rendering
them separately does not produce that even from identical inputs — the two
supplied source plates were encoded independently and differ by a mean of 11.7
in the overlap, which is exactly the failure mode.
**Changes if:** never. `verify_plates.py` exists to catch it; sliced-from-one-master
reports 0.000, independently encoded reports 11.7 and fails.

### The overlap ships at full brightness, with no blend ramp.
**Because** the supplied plates do exactly that — column means through all 1000
overlap columns sit at ratio 0.98–1.04 with no ramp. The soft edge is applied
downstream by the media server.
**Changes if:** the producer says their server expects pre-blended content. Ask
before assuming; baking a ramp into a server that also blends would double-darken
the seam.

### Frame numbers follow the ten-minute loop (4770–7229), not 0.
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

### Animation locks to 30-frame increments between 3:22 and 3:44.
**Because** every cut the plate makes in that window lands on an exact whole
second — a 1 Hz grid. After 3:51 it breaks the grid and strobes, so go free there.

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
