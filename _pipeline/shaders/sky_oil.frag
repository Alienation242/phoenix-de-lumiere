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
uniform float uPaneSplit;     // opening index above which it is a LOWER window
uniform float uBevel;         // bevel width, as a fraction of one pane
uniform float uBevelDepth;    // how far the bevel tips the normal
uniform float uMullion;       // bar width, as a fraction of one pane
uniform float uMullionDark;   // how much the bars take out of the glass
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
void paneGrid(vec2 ouv, float rows, out vec2 tilt, out float bar) {
    tilt = vec2(0.0);
    bar = 0.0;
    if (uPanes <= 0.0 || uPaneCols < 1.0 || rows < 1.0) return;

    vec2 c = fract(ouv * vec2(uPaneCols, rows));
    vec2 e = min(c, 1.0 - c);            // distance to this pane's own edges
    float d = min(e.x, e.y);

    // the separation between panes sits on the join
    bar = 1.0 - smoothstep(uMullion * 0.55, uMullion, d);

    // and the glass is chamfered for the last uBevel of each pane, tipping
    // AWAY from the pane's centre so it reads as raised glass in a frame
    float bx = 1.0 - smoothstep(0.0, max(uBevel, 1e-4), e.x);
    float by = 1.0 - smoothstep(0.0, max(uBevel, 1e-4), e.y);
    tilt = vec2(bx * sign(c.x - 0.5), by * sign(c.y - 0.5)) * uBevelDepth;
    tilt *= uPanes;
    bar  *= uPanes;
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

    // The panes. Which row count applies is decided by the opening's own index:
    // the mattes are numbered upper windows first, then lower, then doors, so
    // one threshold separates them without a second texture.
    vec2 paneTilt; float paneBar;
    paneGrid(openUV, (openIdx > uPaneSplit) ? uPaneRowsLow : uPaneRowsUp,
             paneTilt, paneBar);
    lo += paneTilt;
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
