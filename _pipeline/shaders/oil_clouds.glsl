// ---------------------------------------------------------------------------
// Phoenix de Lumiere - SW wall
// Oil film <-> clouds, driven by the supplied noise plate, with per-window
// thresholds for the objects that come out of and go back through the windows.
//
// TouchDesigner GLSL TOP (pixel shader).
//
// INPUTS
//   sTD2DInputs[0]   the noise plate            (Movie File In TOP)
//   sTD2DInputs[1]   mask stack, one per channel:
//                       R = wall        (MASK_01)
//                       G = windows     (MASK_02)
//                       B = doors       (MASK_04)
//                       A = projectable (MASK_08)
//                    Build it once with a Reorder TOP and cache it.
//   sTD2DInputs[2]   reference/PxDL_SW_OPENING_ID_9788x2552.png
//                       R = opening index 1..27 (0 = not an opening)
//                       G = u inside that opening, B = v inside that opening
//   sTD2DInputs[3]   reference/PxDL_SW_OPENING_SDF_9788x2552.png
//                       distance from the nearest opening edge,
//                       0 = inside an opening, 255 = 512 px away or more
//
//   Inputs 2 and 3 must be point-sampled. Filter = Nearest on both, and no
//   mipmaps on the ID map or the indices will be interpolated into nonsense.
//
// CUSTOM UNIFORMS  (GLSL TOP "Vectors" page)
//   uTime        float   seconds into your segment (0 .. 80)
//   uMorph       float   0 = oil, 1 = clouds. Drive from noise_arc.csv mean_norm.
//   uArc         float   the plate's own brightness arc 0..1, same source.
//   uFlow        float   overall movement speed, ~0.05 to start
//   uEmerge      float   0..1, how far the per-window emergence has progressed
//   uWindowSpread float  seconds of delay spread across the 27 openings, e.g. 6
//   uLevels      float   colour quantisation steps per channel (32 = PSX, 0 = off)
//   uGrid        float   dither cell in px. Match your render scale so the
//                        pattern locks to the pixel grid instead of beating
//                        against the plate's own dither.
//
// The plate ramps mean luma 0.088 -> 0.640 across your 80 s. Feeding that into
// uMorph makes the oil->cloud change land when the whole hall brightens.
// ---------------------------------------------------------------------------

uniform float uTime;
uniform float uMorph;
uniform float uArc;
uniform float uFlow;
uniform float uEmerge;
uniform float uWindowSpread;
uniform float uLevels;
uniform float uGrid;

in vec3 vUV;
out vec4 fragColor;

const float OPENING_COUNT = 27.0;
const float SDF_RANGE     = 512.0;   // px encoded into 0..1 by build_masks.py

// --- value noise / fbm ------------------------------------------------------

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

float fbm(vec2 p, int oct, float lac, float gain) {
    float s = 0.0, amp = 0.5, nrm = 0.0;
    for (int i = 0; i < 8; ++i) {
        if (i >= oct) break;
        s += amp * vnoise(p);
        nrm += amp;
        p *= lac;
        amp *= gain;
    }
    return s / max(nrm, 1e-5);
}

// --- thin-film interference -------------------------------------------------

vec3 thinFilm(float thickness, float cosT) {
    vec3 lambda = vec3(612.0, 549.0, 465.0);     // nm, roughly R G B
    float n = 1.42;                              // oil index
    vec3 phase = 6.2831853 * (2.0 * n * thickness * cosT) / lambda;
    vec3 c = 0.5 + 0.5 * cos(phase);
    return c * c;                                // sharpen the bands
}

// --- ordered dither ---------------------------------------------------------

float bayer4(vec2 px) {
    const float m[16] = float[16](
         0.0,  8.0,  2.0, 10.0,
        12.0,  4.0, 14.0,  6.0,
         3.0, 11.0,  1.0,  9.0,
        15.0,  7.0, 13.0,  5.0);
    ivec2 c = ivec2(mod(floor(px), 4.0));
    return m[c.y * 4 + c.x] / 16.0 - 0.5;
}

void main() {
    vec2 uv  = vUV.st;
    vec2 res = vec2(textureSize(sTD2DInputs[0], 0));
    vec2 p   = vec2(uv.x * (res.x / res.y), uv.y);   // square up so noise is isotropic

    // ---- the driver -------------------------------------------------------
    vec3  plate = texture(sTD2DInputs[0], uv).rgb;
    float L     = dot(plate, vec3(0.2126, 0.7152, 0.0722));

    vec4  msk   = texture(sTD2DInputs[1], uv);
    float mWall = msk.r, mWin = msk.g, mDoor = msk.b, mProj = msk.a;

    // ---- per-opening addressing -------------------------------------------
    vec3  idTex   = texture(sTD2DInputs[2], uv).rgb;
    float openIdx = floor(idTex.r * 255.0 + 0.5);    // 0 = not an opening
    vec2  openUV  = idTex.gb;                        // local UV inside the opening
    bool  inOpen  = openIdx > 0.5;

    // every opening gets its own phase, so window 7 is not window 6
    float idN   = inOpen ? (openIdx - 1.0) / max(OPENING_COUNT - 1.0, 1.0) : 0.0;
    float jitter = hash21(vec2(openIdx, 17.0));      // stable per-opening random
    float delay  = (idN * 0.6 + jitter * 0.4) * uWindowSpread;
    float local  = clamp(uTime - delay, 0.0, 1e6);

    // distance from the nearest opening edge, in pixels
    float edgeDist = texture(sTD2DInputs[3], uv).r * SDF_RANGE;

    float t = uTime * uFlow;

    // ---- domain warp, parallaxed by the plate -----------------------------
    vec2 q = vec2(fbm(p * 3.0 + vec2(0.0, t), 4, 2.0, 0.5),
                  fbm(p * 3.0 + vec2(5.2, 1.3 - t), 4, 2.0, 0.5));
    vec2 warp = p * 2.5 + 1.6 * (q - 0.5) + vec2(t * 0.5, 0.0);
    warp += (L - 0.5) * 0.45;                    // the plate drives the parallax

    // ---- OIL --------------------------------------------------------------
    float h = fbm(warp * 2.0, 6, 2.1, 0.55);
    h = h * 0.65 + L * 0.35;
    float cosT = clamp(0.55 + 0.45 * cos((uv.x - 0.5) * 3.14159), 0.2, 1.0);
    vec3 oil = thinFilm(180.0 + 620.0 * h, cosT);

    float e = 6.0 / max(res.x, 1.0);
    float hx = fbm((warp + vec2(e, 0.0)) * 2.0, 4, 2.1, 0.55) - h;
    float hy = fbm((warp + vec2(0.0, e)) * 2.0, 4, 2.1, 0.55) - h;
    float sheen = pow(clamp(1.0 - length(vec2(hx, hy)) * 22.0, 0.0, 1.0), 6.0);
    oil = oil * (0.35 + 0.85 * h) + sheen * 0.30;

    // ---- CLOUDS -----------------------------------------------------------
    float d = fbm(warp * 1.25 + vec2(0.0, -t * 1.7), 7, 2.0, 0.52);
    d = smoothstep(0.34, 0.86, d * 0.7 + L * 0.45);
    float dUp = fbm((warp + vec2(0.0, 0.055)) * 1.25 + vec2(0.0, -t * 1.7), 5, 2.0, 0.52);
    float shade = clamp(1.0 - max(dUp - d, 0.0) * 3.4, 0.25, 1.0);
    vec3 clouds = mix(vec3(0.16, 0.19, 0.26), vec3(1.00, 0.97, 0.92), shade) * d;

    // ---- morph ------------------------------------------------------------
    // cross-fade through a turbulent band so the two states tear into each
    // other rather than dissolving
    float band = smoothstep(uMorph - 0.22, uMorph + 0.22,
                            h * 0.5 + d * 0.5 + (fbm(warp * 4.0, 3, 2.0, 0.5) - 0.5) * 0.35);
    vec3 col = mix(oil, clouds, band);

    // ---- regions ----------------------------------------------------------
    // openings read darker and colder, so the 3D layer has something to come
    // out OF. openUV.y lets the inside of each window fall off with depth.
    if (inOpen) {
        float depth = 1.0 - openUV.y;                  // 1 at the top of the opening
        vec3 inside = col * 0.30 + vec3(0.02, 0.04, 0.07);
        inside *= mix(0.55, 1.0, depth);
        inside += pow(clamp(d, 0.0, 1.0), 3.0) * 0.35;
        col = mix(col, inside, max(mWin, mDoor));
    }
    col *= mix(1.0, 1.08, mWall);

    // ---- emergence --------------------------------------------------------
    // a wave that grows outward from each opening. Feed the same uEmerge and
    // the same per-opening delay to your 3D layer and the two stay in step.
    float reach  = uEmerge * 420.0;                                  // px
    float wave   = 1.0 - smoothstep(reach - 90.0, reach, edgeDist);
    float pulse  = 0.5 + 0.5 * sin(local * 1.7 + idN * 6.2831);
    col += wave * pulse * 0.18 * vec3(0.55, 0.75, 1.0) * (1.0 - mProj * 0.0);

    // ---- grade to the plate's own arc -------------------------------------
    // your wall must never be brighter than the hall around it
    col *= mix(0.20, 1.15, uArc);

    // ---- PSX colour depth + ordered dither --------------------------------
    if (uLevels > 1.5) {
        float dith = bayer4(gl_FragCoord.xy / max(uGrid, 1.0)) / uLevels;
        col = floor(clamp(col + dith, 0.0, 1.0) * uLevels + 0.5) / uLevels;
    }

    // ---- never light the non-projected areas ------------------------------
    col *= mProj;

    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
