#version 330
// ---------------------------------------------------------------------------
// Phoenix de Lumiere - SW wall - background pass
// Sky with clouds on the inside of the venue; the openings become oil fields.
//
// The sky, the clouds and the thin film are your own PS1_SKY_FRAGMENT, moved
// into lib_common.glsl so the chrome objects reflect the same world.
//
// Two things changed from the first version:
//
//  1. THE OIL IS NOW PER-OPENING. It used to take its view angle from the
//     pixel's position across the whole 9788 px wall, so windows near the
//     edges sat at grazing incidence, fresnel() went to 1 and they blew out
//     white while the middle ones stayed colourful. Each opening now gets its
//     own local -1..1 sweep out of the opening-ID map, so window 1 and window
//     27 show the same range of film. A per-opening phase keeps them from
//     being identical.
//
//  2. FAKE REVEALS. Each opening is given a recessed inner jamb whose visible
//     side follows the camera. Move the camera right and you see the left
//     jamb, exactly as you would through a real hole in a thick wall. This is
//     the single strongest depth cue available on a flat wall, and unlike a
//     shifted silhouette it is contained entirely inside the opening, so it can
//     never misregister against real architecture.
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
uniform float uCamX;          // camera offset in canvas px, signed
uniform float uCamAmp;        // its amplitude, for normalising
uniform float uSkyToOil;
uniform float uCloud;
uniform float uCloudSpeed;
uniform float uHorizon;
uniform float uBands;
uniform float uPlateMix;
uniform vec3  uColor;
uniform float uSpread;
uniform float uSkyGain;
uniform float uOilGain;
uniform float uLevels;
uniform float uGrid;
uniform float uReveal;        // jamb depth, in local opening uv
uniform float uOilSweep;      // how far the per-opening view angle is allowed to swing
uniform float uFilmMin;
uniform float uFilmMax;
uniform float uParSky;        // sky parallax, px per unit uCamX
uniform float uParIn;         // opening-interior parallax

void main() {
    vec2 uv = vUv;
    float aspect = uRes.x / uRes.y;
    vec2  px     = vec2(1.0) / uRes;

    // Masks are the PHYSICAL wall and are sampled unshifted, always. Only what
    // is seen on or through them is allowed to move with the camera.
    vec4  msk   = texture(uMasks, uv);
    float mWin  = msk.g, mDoor = msk.b, mProj = msk.a;
    vec4  aux   = texture(uAux, uv);
    float sdf   = aux.r;

    vec3  plate = texture(uPlate, uv).rgb;
    float L     = dot(plate, vec3(0.2126, 0.7152, 0.0722));

    vec3  idTex   = texture(uOpenId, uv).rgb;
    float openIdx = floor(idTex.r * 255.0 + 0.5);
    vec2  openUV  = idTex.gb;
    float inOpen  = step(0.5, openIdx);

    float camN = uCamX / max(uCamAmp, 1e-3);        // -1..1

    // ---- the wall as a view onto a sky ------------------------------------
    vec2 skyUv = uv + vec2(uCamX * uParSky, 0.0) * px;
    vec2 s = vec2((skyUv.x - 0.5) * aspect,
                  (skyUv.y - uHorizon) / max(1.0 - uHorizon, 1e-3));
    vec3 dir = normalize(vec3(s.x * uSpread, s.y, 1.0));

    // Clouds drift with the plate's own time. soft=0 keeps their 12 hard steps.
    vec3 sky = skyEnv(dir, uTime, uBands, uCloud, uCloudSpeed, uColor, uSkyGain, 0.0);

    // ---- the oil ----------------------------------------------------------
    // Two coordinate systems, deliberately:
    //
    //  VIEW ANGLE is local to each opening but COMPRESSED by uOilSweep, so no
    //  window ever reaches grazing incidence. Driving it from the pixel's
    //  position across the whole 9788 px wall is what used to send fresnel() to
    //  1 at the edges and blow the outer windows out to white.
    //
    //  FILM THICKNESS comes from one continuous field across the entire canvas.
    //  Taking it from the local angle instead would give all 27 openings the
    //  same dark-centre-bright-edge stamp; this way neighbouring windows show
    //  different parts of one slick that flows across the whole wall.
    vec2 lo = (openUV * 2.0 - 1.0) * uOilSweep;
    lo.x += uCamX * uParIn * px.x * 8.0;
    vec3 odir  = normalize(vec3(lo.x, lo.y, 1.0));
    vec3 onrm  = -odir;
    vec3 oview = normalize(vec3(lo.x * 0.5, lo.y * 0.5, -1.0));
    float cosTheta = clamp(abs(dot(onrm, oview)), 0.45, 1.0);

    vec2 flow = uv + vec2(uCamX * uParIn * px.x * 4.0, 0.0);
    vec3 noisePos = vec3(flow.x * 11.0, flow.y * 3.0, uTime * 0.11);
    float noiseVal = fbm(noisePos) * 0.5 + 0.5;
    float pool = mix(1.0, 0.62, openUV.y);
    float thickness = mix(uFilmMin, uFilmMax, noiseVal * 0.72 + L * 0.28) * pool;

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
    // Move right and the LEFT jamb comes into view, and vice versa. The jamb is
    // the opening's content pushed back and darkened, so it still carries oil.
    float wL = max( camN, 0.0) * uReveal;
    float wR = max(-camN, 0.0) * uReveal;
    float revL = 1.0 - smoothstep(0.0, max(wL, 1e-4), openUV.x);
    float revR = 1.0 - smoothstep(0.0, max(wR, 1e-4), 1.0 - openUV.x);
    float reveal = max(revL, revR) * inOpen * step(0.004, wL + wR);

    // the jamb is in shadow, and darker the deeper in it goes
    float jambSide = (wL > wR) ? openUV.x / max(wL, 1e-4) : (1.0 - openUV.x) / max(wR, 1e-4);
    float jamb = mix(0.28, 0.85, clamp(jambSide, 0.0, 1.0));
    col = mix(col, col * jamb, reveal);

    // the outer corner of the opening catches light on the opposite side
    float lipW = 0.010;
    float lip = (camN > 0.0) ? 1.0 - smoothstep(0.0, lipW, 1.0 - openUV.x)
                             : 1.0 - smoothstep(0.0, lipW, openUV.x);
    col += uColor * lip * inOpen * abs(camN) * 0.30;

    // a thin ambient-occlusion line hugging every opening edge, on the wall
    // side. sdf is 0 at the edge and rises inwards.
    float edgeAO = (1.0 - smoothstep(0.0, 0.06, sdf)) * (1.0 - inOpen);
    col *= 1.0 - 0.35 * edgeAO;

    // ---- sit ON the plate, never instead of it ----------------------------
    float pl = pow(L, 0.8);
    col *= mix(1.0, 0.50 + 0.85 * pl, uPlateMix);
    col += pow(L, 4.0) * 0.10 * uPlateMix;

    col *= mix(0.25, 1.10, uArc);

    col = quantise(col, uLevels, uGrid, gl_FragCoord.xy);
    col *= mProj;
    fragColor = vec4(col, 1.0);
}
