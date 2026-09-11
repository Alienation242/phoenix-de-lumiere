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
uniform float uFilmMin;
uniform float uFilmMax;
uniform float uLevels;
uniform float uGrid;
uniform float uReveal;
uniform float uParIn;
uniform float uIntro;         // 0 = the bare shared noise, 1 = the full treatment
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

    vec3 zenith  = uColor * 0.52 + vec3(0.012, 0.018, 0.028);
    vec3 horizon = uColor * 0.13 + vec3(0.016, 0.022, 0.032);
    vec3 sky = mix(horizon, zenith, v) * uSkyGain;

    // ---- oil ---------------------------------------------------------------
    // The plate drives the film thickness, so the interference colours follow
    // the dither. The angle term only tilts the rainbow across each opening,
    // clamped well away from grazing so no window blows out white.
    vec2 lo = (openUV * 2.0 - 1.0) * uOilSweep;
    lo.x += uCamX * uParIn * px.x * 8.0;
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

    float oilAmount = max(mWin, mDoor * 0.6) * uSkyToOil * inOpen;
    vec3 col = mix(sky, oilColor, oilAmount);

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

    col *= mix(0.25, 1.10, uArc);          // never brighter than the hall

    col = quantise(col, uLevels, uGrid, gl_FragCoord.xy);

    // Hand-off. At uIntro 0 this wall is EXACTLY the shared plate, pixel for
    // pixel, so the segment begins from the same image the other eleven
    // surfaces are showing and grows out of it. Applied after quantise so the
    // untouched plate is not posterised on the way through.
    col = mix(plate, col, uIntro);

    col *= mProj;                          // never light the non-projected areas
    fragColor = vec4(col, 1.0);
}
