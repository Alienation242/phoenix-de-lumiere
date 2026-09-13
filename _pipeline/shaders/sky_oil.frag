#version 330
// ---------------------------------------------------------------------------
// Phoenix de Lumiere - SW wall - background pass
//
// THE PLATE DRIVES EVERYTHING HERE. There is no procedural noise in this
// shader at all - no fbm, no clouds, nothing generated. The noise plate's own
// dither is the only source of texture on the wall, and it is used as an INPUT
// to the sky and to the oil rather than being composited under them.
//
// That matters because the plate runs across all twelve mapped surfaces and is
// what ties the whole piece together. Generating a second, unrelated cloud
// field on top of it put SW in a different world from the other eleven walls,
// and buried the one thing they share.
//
// How it works now:
//
//   sky   the vertical gradient only places the horizon. The BAND a pixel
//         lands in is driven by the plate's luminance, so every dither dot
//         steps the colour. The dither becomes the sky's structure.
//
//   oil   the thin film's THICKNESS is driven by the plate's luminance, so the
//         interference colours follow the dither directly. The per-opening view
//         angle is still there but only sweeps a narrow, clamped range - enough
//         to tilt the rainbow, not enough to blow the outer windows to white.
//
// fresnel / thinFilmReflectance are still yours, verbatim from
// shaderRefs/engine/shaders.ts.
//
// One consequence worth knowing: driving colour bands straight from plate luma
// AMPLIFIES the plate's compression. The supplied mp4s are 0.019 bits/pixel
// with measurable DCT blocking (spec section 3), and that blocking now steps
// the sky. It is another reason the final render wants the ProRes masters.
// ---------------------------------------------------------------------------
#include "lib_common.glsl"

in  vec2 vUv;
out vec4 fragColor;

uniform sampler2D uPlate;     // the noise plate, stitched to the full canvas
uniform sampler2D uMasks;     // R wall, G windows, B doors, A projectable
uniform sampler2D uOpenId;    // R opening index/255, G/B local uv in that opening
uniform sampler2D uAux;       // R sdf, G column, B trim, A all openings

uniform vec2  uRes;
uniform float uTime;
uniform float uArc;           // plate brightness arc 0..1 (noise_arc.csv)
uniform float uCamX;
uniform float uCamAmp;
uniform float uSkyToOil;
uniform float uHorizon;
uniform float uBands;
uniform float uPlateDrive;    // how hard the plate drives the sky bands
uniform float uPlateMix;      // residual contrast multiply on top
uniform vec3  uColor;
uniform float uSpread;
uniform float uSkyGain;
uniform float uOilGain;
uniform float uOilSweep;
uniform float uOilBleed;      // 0 = oil lives in the openings, 1 = everywhere

// ---- the leaded grid inside every window --------------------------------
// The pane COUNT comes from the artist's sketch; the row count for the upper
// windows comes from measuring the mattes. With 4 columns the rectangular part
// of a lower window is 4 x 8 panes at 149 x 138 canvas px - 8 % off square -
// and an upper window is 4 x 6 at 83 x 80 px, 4 % off. Square panes are
// therefore a real property of this facade, not an assumption, which is what
// makes one pane a usable ruler for the wall's real size.
uniform float uPanes;         // master: 0 turns the whole grid off
uniform float uPaneCols;      // panes across an opening
uniform float uPaneRowsUp;    // rows in an upper window
uniform float uPaneRowsLow;   // rows in a lower window
uniform float uPaneSplit;
uniform float uDoorFirst;     // the first opening index that is a door, from openings.json     // opening index above which it is a LOWER window
uniform float uBevel;         // bevel width, as a fraction of one pane
uniform float uBevelDepth;    // how far the bevel tips the normal
uniform float uMullion;       // bar width, as a fraction of one pane
uniform float uMullionDark;   // how much the bars take out of the glass
uniform float uArchUp;        // how much of an UPPER window is arch head, 0..1
uniform float uArchLow;       // the same for a lower window. ~0: no fanlight
uniform float uFanArc;        // the fanlight's inner arc, as a fraction of the
                              // head radius

// Where the light is coming from, in wall space: x across, y up. It is a
// keyframed track like everything else, so the sun crosses the wall over the
// piece instead of sitting still - and the shading on every chamfer and every
// flute crosses with it. Slowly: one sweep in a hundred seconds on a wall this
// size is a drift you notice having happened, not a movement you watch.
uniform vec2  uSunDir;
uniform float uSunShade;      // how hard the sun shades the pane chamfers

// ---- the glass remembers being passed through -----------------------------
// An object crossing the wall plane leaves a ring expanding from where it went
// through. Positions are in CANVAS px, not uv, because uv on a 9788x2552 wall
// is anisotropic by nearly four to one and a ring drawn in it would be an
// ellipse. uImpact is xy = where, z = seconds since, w = the object's radius.
uniform vec2  uCanvas;
uniform int   uImpactN;
uniform vec4  uImpact[8];
uniform float uRippleAmp;     // how far a ripple tips the glass
uniform float uRippleFreq;    // radians per canvas px
uniform float uRippleOmega;   // radians per second, = speed * freq
uniform float uRippleSpread;  // how far the disturbance reaches, in radii
uniform float uRippleLife;    // seconds before it has gone
uniform float uRippleWarp;    // how far it DRAGS the glass, in opening widths
uniform float uRippleShade;   // extra weight on the light swing it causes
uniform float uRippleGrow;    // how fast the disturbed area spreads. 0 = all at once
uniform float uFilmMin;
uniform float uFilmMax;
uniform float uLevels;
uniform float uGrid;
uniform float uReveal;
uniform float uParIn;
uniform float uIntro;         // 0 = the bare shared noise, 1 = the full treatment
uniform float uSaturation;
uniform vec3  uDoorTone;      // the doors are not oil and not sky
uniform float uDoorGain;
uniform float uArcFloor;      // how dark the wall is allowed to get at uArc = 0
uniform float uPlateMean;     // THIS frame's mean luma, straight from noise_arc.csv
uniform float uPlateContrast; // how hard the dither swings around that mean

// The plate's structure, measured against the frame's own average rather than
// against absolute black.
//
// The segment spends seventy seconds at a mean luma of 0.11. Driving the colour
// bands from raw luminance there puts every pixel in the same band and the wall
// goes flat - the dither is still present in the signal, it just has nowhere to
// go. Centring on the frame mean keeps the dither driving the image at every
// brightness, while absolute luma still sets the overall level further down, so
// a dark passage stays dark.
float rel(float x) {
    return clamp(0.5 + (x - uPlateMean) * uPlateContrast, 0.0, 1.0);
}

// A leaded grid inside one opening. openUV is already 0..1 across THIS
// opening whatever size it is, so the same call lands the grid correctly on
// every window without knowing anything about where it sits on the wall.
//
// It returns a TILT rather than a full normal: the oil below already builds a
// view vector out of the opening's UV, and adding the tilt into that is what
// makes each pane catch the interference colours at its own angle. That is the
// bevel - not a painted highlight, an actual change of surface direction.
// The arch head of an upper window is a FANLIGHT, not more grid: a concentric
// inner arc, the centre mullion carried up to the apex, and one spoke into each
// spandrel. Straight off the photograph of the venue wall - guessing it as
// four more grid squares under a curve was wrong, and it was the first thing
// the real window disagreed with.
//
// Head space: x runs -1..1 across the opening, y 0 at the springing line and 1
// at the apex, so r = 1 is the arch itself and the geometry is the same for
// every window whatever its size.
void fanLight(vec2 ouv, float archFrac, out vec2 tilt, out float bar,
              out vec2 cell) {
    tilt = vec2(0.0);
    bar = 0.0;
    cell = vec2(0.0);
    float hx = ouv.x * 2.0 - 1.0;
    float hy = 1.0 - clamp(ouv.y / max(archFrac, 1e-4), 0.0, 1.0);
    float r = length(vec2(hx, hy));
    float ang = atan(max(hy, 0.0), hx);        // 0 right, PI/2 up, PI left

    // bar half-width, converted from "fraction of a pane" into head units so a
    // fanlight bar is the same thickness on the wall as a grid bar
    float w = max(uMullion / max(uPaneCols, 1.0), 1e-4);

    float dArc = abs(r - uFanArc);
    float dMid = (r > uFanArc) ? abs(hx) : 1e3;
    float dSpk = 1e3;
    if (r > uFanArc) {
        // 0.7853982 = PI/4, 2.3561945 = 3PI/4. Multiplying by r turns the
        // angular gap into a real distance, so a spoke keeps its width.
        dSpk = min(abs(ang - 0.7853982), abs(ang - 2.3561945)) * r;
    }
    float d = min(dArc, min(dMid, dSpk));

    bar = (1.0 - smoothstep(w * 0.55, w, d)) * uPanes;
    // The chamfer falls away from whichever bar is nearest. Radially is close
    // enough at this size, and it is the arc that carries the read.
    float ch = (1.0 - smoothstep(0.0, max(uBevel / max(uPaneCols, 1.0), 1e-4), d))
             * uBevelDepth * uPanes;
    vec2 rad = normalize(vec2(hx, -hy) + vec2(1e-5));
    tilt = rad * ch * sign(uFanArc - r);
    cell = vec2(hx, -hy);
}

// The ring an object leaves in the glass on its way through.
//
// Returned RAW, without the amplitude, because three different things are made
// out of it and they want their own weights:
//
//   * uRippleAmp   tips the normal, which recolours the interference. This one
//                  saturates: the angle term is clamped at 0.45 and the tilt
//                  feeds a normalize(), so past about 1.4 the colour stops
//                  moving and starts folding back on itself. More is not more.
//   * uRippleWarp  DRAGS the opening's own coordinates, so the leaded bars and
//                  the chamfers bend with the wave. Nothing saturates here -
//                  it is the window itself deforming, which is the one that
//                  reads as the room morphing from across a hall.
//   * uRippleShade swings the sun shading harder without touching either.
//
// The plate is NOT warped. It is the shared noise and it stays where it is -
// see the note at the top of main().
vec2 rippleField(vec2 atPx) {
    vec2 acc = vec2(0.0);
    if (uRippleAmp <= 0.0 && uRippleWarp <= 0.0) return acc;
    for (int i = 0; i < uImpactN; i++) {
        vec2  c    = uImpact[i].xy;
        float age  = uImpact[i].z;
        float rad  = max(uImpact[i].w, 1.0);
        vec2  dv   = atPx - c;
        float d    = length(dv);
        // Concentric rings that travel outward but stay ANCHORED to where
        // the object went through, and die away in place.
        //
        // The first version was a single crest at age * speed, which is what
        // a real impact front does and is useless here: at 900 px/s it left a
        // 332 px window in a third of a second and spent the rest of its life
        // ringing solid masonry two thousand pixels away. Measured at a
        // typical frame, the crest was at 2406 px and the window it came from
        // saw nothing at all.
        //
        // So the PHASE travels - the rings still move outward - while the
        // envelope hangs on the impact point and fades. That is the drop-in-
        // water read, and it stays where the object actually was.
        float reach = max(rad * uRippleSpread, 1.0);
        float atten = exp(-d / reach);
        float life  = exp(-age / max(uRippleLife, 1e-3));

        // The disturbance GROWS. Without this the whole ring field switched on
        // at its full extent the instant the object touched the glass, which is
        // the one thing water never does - a drop starts as a point.
        //
        // The front is measured in reaches per lifetime rather than px/s, so it
        // scales with the object: a big shape disturbs a big area just as fast
        // as a small one disturbs a small one, which is what a bigger splash
        // looks like. 1.0 means the front crosses the whole reach exactly once
        // before the ripple has died.
        //
        // The front is SLOWER than the phase, so crests keep welling up in the
        // middle and dying as they reach the rim, the way they do in water.
        if (uRippleGrow > 0.0) {
            float front = reach * uRippleGrow * age / max(uRippleLife, 1e-3);
            float soft  = max(reach * 0.22, 1.0);
            atten *= smoothstep(front + soft, front - soft, d);
        }

        acc += normalize(dv + vec2(1e-5))
             * sin(d * uRippleFreq - age * uRippleOmega) * atten * life;
    }
    return acc;
}

// The leaded grid inside one opening. openUV is already 0..1 across THIS
// opening whatever size it is, so the same call lands the grid correctly on
// every window without knowing anything about where it sits on the wall.
//
// It returns a TILT rather than a full normal: the oil below already builds a
// view vector out of the opening's UV, and adding the tilt into that is what
// makes each pane catch the interference colours at its own angle. That is the
// bevel - not a painted highlight, an actual change of surface direction.
//
// The chamfer tips TOWARD each pane's centre, because the panes on the real
// wall are recessed and the bars stand at the wall plane. A negative
// --bevel-depth turns them back into raised panels.
void paneGrid(vec2 ouv, float rows, float archFrac, out vec2 tilt,
              out float bar, out vec2 cell) {
    tilt = vec2(0.0);
    bar = 0.0;
    cell = vec2(0.0);
    if (uPanes <= 0.0 || uPaneCols < 1.0 || rows < 1.0) return;

    // Above the springing line is the head. The grid gets the rectangular part
    // ONLY - rows counted from the springing down, which is how the window was
    // built and how it was counted off the photograph.
    if (archFrac > 0.001) {
        if (ouv.y < archFrac) { fanLight(ouv, archFrac, tilt, bar, cell); return; }
        ouv.y = (ouv.y - archFrac) / max(1.0 - archFrac, 1e-4);
    }

    vec2 c = fract(vec2(ouv.x * uPaneCols, ouv.y * rows));
    vec2 e = min(c, 1.0 - c);            // distance to this pane's own edges
    float d = min(e.x, e.y);

    // the separation between panes sits on the join
    bar = 1.0 - smoothstep(uMullion * 0.55, uMullion, d);

    float bx = 1.0 - smoothstep(0.0, max(uBevel, 1e-4), e.x);
    float by = 1.0 - smoothstep(0.0, max(uBevel, 1e-4), e.y);
    tilt = vec2(bx * sign(0.5 - c.x), by * sign(0.5 - c.y)) * uBevelDepth;
    tilt *= uPanes;
    bar  *= uPanes;
    cell = (c - 0.5) * 2.0;          // -1..1 inside this pane, for the shadow
}


void main() {
    vec2 uv = vUv;
    vec2 px = vec2(1.0) / uRes;

    // Masks are the PHYSICAL wall and are sampled unshifted, always. Only what
    // is seen on or through them is allowed to move with the camera.
    vec4  msk  = texture(uMasks, uv);
    float mWin = msk.g, mDoor = msk.b, mProj = msk.a;
    float sdf  = texture(uAux, uv).r;

    vec3  idTex   = texture(uOpenId, uv).rgb;
    float openIdx = floor(idTex.r * 255.0 + 0.5);
    vec2  openUV  = idTex.gb;
    float inOpen  = step(0.5, openIdx);

    // A DOOR IS A DOOR ALL THE WAY TO ITS EDGE.
    //
    // The authored mask draws the left and right doors with a band of WINDOW
    // wrapped around the leaf - 28,000 and 33,000 px of it - which the notes
    // call the inside of the cut door frame. Taken at face value that band got
    // the full glass treatment, and what you saw on the wall was a stripe of
    // oil colours running down the side of each of those two doors. The middle
    // door has only a 6 px sliver of it, which is why only two of the three
    // ever showed it.
    //
    // Decided from the opening's own INDEX rather than from the mattes: the
    // doors are the last openings in openings.json and the renderer passes the
    // first of them in. So the whole of a door opening is door, whatever the
    // colour mask says about parts of it, and the authored mask is still not
    // edited on disk.
    float isDoor = inOpen * step(uDoorFirst - 0.5, openIdx);
    mDoor = max(mDoor, isDoor);
    mWin  = min(mWin, 1.0 - isDoor);

    float camN = uCamX / max(uCamAmp, 1e-3);

    // ---- the plate, the only texture on this wall -------------------------
    // SAMPLED AT ITS OWN PIXEL. Never offset, by anything, for any reason.
    //
    // This wall is one of twelve sharing this noise, and the noise is what makes
    // the twelve read as one room. Sliding the sample to fake sky parallax - as
    // this did - slides the shared image out of register with the other eleven
    // surfaces. Parallax has to come from things this wall owns: the objects,
    // the jamb reveals, the pillars' shading, the tilt of the oil. Not from the
    // plate.
    vec3  plate = texture(uPlate, uv).rgb;
    float L     = dot(plate, vec3(0.2126, 0.7152, 0.0722));

    // ---- sky ---------------------------------------------------------------
    // The gradient places the horizon and nothing else. Everything you can SEE
    // is the plate stepping through the bands.
    float grad = clamp((uv.y - uHorizon) / max(1.0 - uHorizon, 1e-3)
                       * 0.5 + 0.5, 0.0, 1.0);
    float v = mix(grad, rel(L), uPlateDrive);
    v = floor(v * uBands + 0.5) / uBands;

    // A much wider spread between the two ends. The bands are driven by the
    // plate, so the distance between zenith and horizon IS the contrast of
    // everything the dither does on this wall - compress it and the whole
    // surface goes flat however much gain you add afterwards.
    vec3 zenith  = uColor * 0.92 + vec3(0.020, 0.030, 0.048);
    vec3 horizon = uColor * 0.09 + vec3(0.010, 0.014, 0.022);
    vec3 sky = mix(horizon, zenith, v) * uSkyGain;

    // ---- oil ---------------------------------------------------------------
    // The plate drives the film thickness, so the interference colours follow
    // the dither. The angle term only tilts the rainbow across each opening,
    // clamped well away from grazing so no window blows out white.
    vec2 lo = (openUV * 2.0 - 1.0) * uOilSweep;
    lo.x += uCamX * uParIn * px.x * 8.0;
    // lo is finished below, once the ripple's warp is known.

    // The panes. Which row count applies is decided by the opening's own index:
    // the mattes are numbered upper windows first, then lower, then doors, so
    // one threshold separates them without a second texture.
    // Only in the glass. A door is not a pane and the masonry does not ring.
    vec2 rip = rippleField(uv * uCanvas) * mWin * inOpen;

    // The warp goes in FIRST, on the coordinates the grid is built from, so the
    // bars and the chamfers are drawn already bent. Adding it afterwards would
    // only shade a straight grid, which is the difference between the glass
    // moving and a pattern sliding over it.
    vec2 ouvR = clamp(openUV + rip * uRippleWarp, 0.0, 1.0);

    vec2 paneTilt, paneCell; float paneBar;
    bool isLow = (openIdx > uPaneSplit);
    paneGrid(ouvR, isLow ? uPaneRowsLow : uPaneRowsUp,
             isLow ? uArchLow : uArchUp, paneTilt, paneBar, paneCell);
    vec2 ripTilt = rip * uRippleAmp;
    lo += paneTilt + ripTilt;
    vec3 odir  = normalize(vec3(lo.x, lo.y, 1.0));
    vec3 onrm  = -odir;
    vec3 oview = normalize(vec3(lo.x * 0.5, lo.y * 0.5, -1.0));
    float cosTheta = clamp(abs(dot(onrm, oview)), 0.45, 1.0);

    float pool = mix(1.0, 0.62, openUV.y);
    float thickness = mix(uFilmMin, uFilmMax, rel(L)) * pool;

    float r = thinFilmReflectance(cosTheta, 650.0, thickness);
    float g = thinFilmReflectance(cosTheta, 510.0, thickness);
    float b = thinFilmReflectance(cosTheta, 475.0, thickness);

    vec3 halfVector = normalize(vec3(1.0, 1.0, 1.0) + oview);
    float specular = pow(max(dot(onrm, halfVector), 0.0), 64.0) * 0.5;

    vec3 oilColor = vec3(r, g, b) * uOilGain + (uColor * specular * 2.0);
    oilColor = oilColor / (oilColor + vec3(1.0));
    // The bars are not glass. They take light out rather than adding a drawn
    // line, so they stay part of the surface at every brightness the day goes
    // through instead of turning into a grid stencilled over the top.
    oilColor *= (1.0 - paneBar * uMullionDark);
    // paneTilt IS the local surface direction, so one dot with the light is
    // the whole lighting model for the chamfers. Nothing is drawn: the faces
    // that turn toward the sun brighten and the ones turning away fall off,
    // which is why the effect moves when the sun does.
    // The chamfers and the ripple are dotted with the light separately so the
    // ripple can be made to swing much harder than the static chamfers without
    // dragging them with it - uSunShade is already "the one number for too
    // much" on those.
    oilColor *= max(0.0, 1.0 + uSunShade * (dot(paneTilt, uSunDir)
                                            + dot(ripTilt, uSunDir) * uRippleShade));

    // And the recess shadow, which is the part you actually SEE move. The
    // chamfer is 12 % of a pane on each side, so re-lighting it alone changed
    // 8 % of the window's pixels and read as nothing at all from across a
    // hall. A sunken panel also pools a shadow against whichever edge the sun
    // is behind - that is a soft gradient across the whole pane, it slides
    // from one side to the other as the sun crosses, and it is what makes this
    // read as light moving rather than as edges twinkling.
    vec2 sunN = normalize(uSunDir + vec2(1e-5));
    float occl = clamp(dot(paneCell, sunN), 0.0, 1.0);
    oilColor *= 1.0 - pow(occl, 3.0) * uSunShade * 0.85 * uPanes;

    // uOilBleed lifts the oil OUT of the windows and across the whole wall.
    // At 0 this line is exactly what it always was. At 1 the thin film covers
    // everything the sky covers - and because the film's THICKNESS is driven by
    // the plate's own luminance, the interference colours then follow the
    // plate's patterns. That is the screen-tearing read: the noise wearing the
    // oil's colours instead of the sky's.
    float oilAmount = mix(mWin * inOpen, 1.0, uOilBleed) * uSkyToOil;
    vec3 col = mix(sky, oilColor, oilAmount);

    // ---- doors: a way out, not a pane of oil -------------------------------
    // They used to take 60% oil and 40% SKY. The sky is the brightest thing on
    // this wall, so every bright dither dot inside a doorway punched through as
    // a vivid blue speck - which is exactly the white-and-blue mess on the
    // doors.
    //
    // A door is a hole to the outside, so it gets its own dark recess instead.
    // The plate still drives it, like everything else here, but over a much
    // narrower range and in its own tone - so a chrome object leaving through
    // the middle door reads against darkness rather than against the sky.
    float doorDepth = mix(1.0, 0.42, openUV.y);
    vec3 doorCol = uDoorTone * (0.16 + 0.62 * rel(L)) * doorDepth * uDoorGain;
    col = mix(col, doorCol, mDoor * inOpen);

    // ---- fake reveals: a thick wall around every opening -------------------
    // Move right and the LEFT jamb comes into view, and vice versa.
    float wL = max( camN, 0.0) * uReveal;
    float wR = max(-camN, 0.0) * uReveal;
    float revL = 1.0 - smoothstep(0.0, max(wL, 1e-4), openUV.x);
    float revR = 1.0 - smoothstep(0.0, max(wR, 1e-4), 1.0 - openUV.x);
    float reveal = max(revL, revR) * inOpen * step(0.004, wL + wR);

    float jambSide = (wL > wR) ? openUV.x / max(wL, 1e-4) : (1.0 - openUV.x) / max(wR, 1e-4);
    float jamb = mix(0.28, 0.85, clamp(jambSide, 0.0, 1.0));
    col = mix(col, col * jamb, reveal);

    float lipW = 0.010;
    float lip = (camN > 0.0) ? 1.0 - smoothstep(0.0, lipW, 1.0 - openUV.x)
                             : 1.0 - smoothstep(0.0, lipW, openUV.x);
    col += uColor * lip * inOpen * abs(camN) * 0.30;

    float edgeAO = (1.0 - smoothstep(0.0, 0.06, sdf)) * (1.0 - inOpen);
    col *= 1.0 - 0.35 * edgeAO;

    // ---- residual contrast -------------------------------------------------
    // The plate is already the structure, so this is only a gentle contrast
    // lift, not the compositing step it used to be.
    col *= mix(1.0, 0.62 + 0.62 * L, uPlateMix);

    col *= mix(uArcFloor, 1.25, uArc);     // never brighter than the hall

    col = mix(vec3(dot(col, vec3(0.2126, 0.7152, 0.0722))), col, uSaturation);
    col = quantise(col, uLevels, uGrid, gl_FragCoord.xy);

    // Hand-off. At uIntro 0 this wall is EXACTLY the shared plate, pixel for
    // pixel, so the segment begins from the same image the other eleven
    // surfaces are showing and grows out of it. Applied after quantise so the
    // untouched plate is not posterised on the way through.
    col = mix(plate, col, uIntro);

    col *= mProj;                          // never light the non-projected areas
    fragColor = vec4(col, 1.0);
}
