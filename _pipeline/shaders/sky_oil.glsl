// ---------------------------------------------------------------------------
// Phoenix de Lumiere - SW wall
// The noise becomes a sky with clouds on the inside of the venue; the windows
// become oil fields.
//
// This is YOUR shader. The hash/noise/fbm, fresnel and thinFilmReflectance
// functions are ported verbatim from shaderRefs/engine/shaders.ts
// (PS1_SKY_FRAGMENT), including the 12-step sky banding. Two changes:
//
//   * their `useLighting` uniform, which cross-fades baseSky <-> oil, is now
//     driven per-pixel by the WINDOW mask. Sky on the wall, oil in the windows,
//     one mix, exactly as they wrote it.
//   * a cloud layer sits in front of the sky, driven by the noise plate so the
//     clouds move with the rest of the building.
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
//   sTD2DInputs[2]   reference/PxDL_SW_OPENING_ID_*.png   (Nearest, NO mipmaps)
//
// UNIFORMS
//   uTime         float  seconds into your segment (0..80)
//   uArc          float  the plate's brightness arc 0..1  (noise_arc.csv mean_norm)
//   uSkyToOil     float  0 = everything sky, 1 = windows fully oil. Ramp it.
//   uCloud        float  0..1 cloud coverage
//   uCloudSpeed   float  ~0.15
//   uHorizon      float  where the horizon sits on the wall, 0..1 (try 0.35)
//   uBands        float  sky quantisation steps (12 = their value)
//   uPlateMix     float  how much of the raw plate shows through, 0..1
//   uColor        vec3   theme colour. their sky tints from this
//   uLevels       float  colour steps per channel (32 = PSX, 0 = off)
//   uGrid         float  dither cell in px; match your render scale
// ---------------------------------------------------------------------------

uniform float uTime;
uniform float uArc;
uniform float uSkyToOil;
uniform float uCloud;
uniform float uCloudSpeed;
uniform float uHorizon;
uniform float uBands;
uniform float uPlateMix;
uniform vec3  uColor;
uniform float uLevels;
uniform float uGrid;

in vec3 vUV;
out vec4 fragColor;

#define PI 3.14159265359

// ---- ported verbatim from PS1_SKY_FRAGMENT --------------------------------

vec3 hash(vec3 p) {
    p = vec3(dot(p, vec3(127.1, 311.7, 74.7)),
             dot(p, vec3(269.5, 183.3, 246.1)),
             dot(p, vec3(113.5, 271.9, 124.6)));
    return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
}

float noise(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    vec3 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(dot(hash(i + vec3(0.0,0.0,0.0)), f - vec3(0.0,0.0,0.0)),
                       dot(hash(i + vec3(1.0,0.0,0.0)), f - vec3(1.0,0.0,0.0)), u.x),
                   mix(dot(hash(i + vec3(0.0,1.0,0.0)), f - vec3(0.0,1.0,0.0)),
                       dot(hash(i + vec3(1.0,1.0,0.0)), f - vec3(1.0,1.0,0.0)), u.x), u.y),
               mix(mix(dot(hash(i + vec3(0.0,0.0,1.0)), f - vec3(0.0,0.0,1.0)),
                       dot(hash(i + vec3(1.0,0.0,1.0)), f - vec3(1.0,0.0,1.0)), u.x),
                   mix(dot(hash(i + vec3(0.0,1.0,1.0)), f - vec3(0.0,1.0,1.0)),
                       dot(hash(i + vec3(1.0,1.0,1.0)), f - vec3(1.0,1.0,1.0)), u.x), u.y), u.z);
}

float fbm(vec3 p) {
    float value = 0.0;
    float amplitude = 0.5;
    float frequency = 2.0;
    for (int i = 0; i < 5; i++) {
        value += amplitude * noise(p * frequency);
        amplitude *= 0.5;
        frequency *= 2.0;
    }
    return value;
}

float fresnel(float cosTheta, float n1, float n2) {
    float r0 = (n1 - n2) / (n1 + n2);
    r0 *= r0;
    return r0 + (1.0 - r0) * pow(1.0 - cosTheta, 5.0);
}

float thinFilmReflectance(float cosTheta1, float wavelength, float thickness) {
    float uAirIOR  = 1.00;
    float uFilmIOR = 1.50;
    float uBaseIOR = 1.33;

    float sinTheta1 = sqrt(max(0.0, 1.0 - cosTheta1 * cosTheta1));
    float sinTheta2 = (uAirIOR / uFilmIOR) * sinTheta1;
    float cosTheta2 = sqrt(max(0.0, 1.0 - sinTheta2 * sinTheta2));

    float opd = 2.0 * uFilmIOR * thickness * cosTheta2;
    float phaseDifference = (2.0 * PI / wavelength) * opd + PI;

    float r12 = fresnel(cosTheta1, uAirIOR, uFilmIOR);
    float r23 = fresnel(cosTheta2, uFilmIOR, uBaseIOR);

    float intensity = r12 + r23 + 2.0 * sqrt(r12 * r23) * cos(phaseDifference);
    return clamp(intensity, 0.0, 1.0);
}

// ---- ordered dither --------------------------------------------------------

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
    float aspect = res.x / res.y;

    vec3  plate = texture(sTD2DInputs[0], uv).rgb;
    float L     = dot(plate, vec3(0.2126, 0.7152, 0.0722));

    vec4  msk   = texture(sTD2DInputs[1], uv);
    float mWin  = msk.g, mDoor = msk.b, mProj = msk.a;

    vec3  idTex   = texture(sTD2DInputs[2], uv).rgb;
    float openIdx = floor(idTex.r * 255.0 + 0.5);
    vec2  openUV  = idTex.gb;

    // ---- the wall as a view onto a sky ------------------------------------
    // The canvas is a flattened elevation, so the vertical axis IS the sky's
    // vertical axis. uHorizon decides how much wall is below the horizon.
    vec2 s = vec2((uv.x - 0.5) * aspect, (uv.y - uHorizon) / max(1.0 - uHorizon, 1e-3));
    vec3 dir = normalize(vec3(s.x * 0.35, s.y, 1.0));

    // their sky, tinted from uColor, quantised into uBands steps
    vec3 zenith  = uColor * 0.35 + vec3(0.01, 0.015, 0.02);
    vec3 horizon = uColor * 0.12 + vec3(0.015, 0.02, 0.03);
    float h = clamp(dir.y * 0.5 + 0.5, 0.0, 1.0);
    h = floor(h * uBands) / uBands;
    vec3 baseSkyColor = mix(horizon, zenith, h);

    // ---- clouds, moving with the plate ------------------------------------
    vec3 cloudP = dir * 3.0 + vec3(uTime * uCloudSpeed, 0.0, uTime * uCloudSpeed * 0.4);
    float cd = fbm(cloudP) * 0.5 + 0.5;
    cd = cd * 0.75 + L * 0.45;                       // the plate drives the clouds
    float cover = smoothstep(1.0 - uCloud, 1.25 - uCloud, cd);
    // banded like the sky, so the clouds read PS1 rather than soft
    cover = floor(cover * uBands) / uBands;
    float lift = fbm(cloudP + vec3(0.0, 0.35, 0.0)) * 0.5 + 0.5;
    vec3 cloudCol = mix(vec3(0.42, 0.46, 0.55), vec3(1.0, 0.97, 0.92), lift);
    vec3 sky = mix(baseSkyColor, cloudCol, cover * 0.85);

    // ---- their oil --------------------------------------------------------
    vec3 viewDir = normalize(vec3(s.x * 0.35, s.y, -1.0));
    vec3 normal  = -dir;
    float cosTheta = max(dot(normal, viewDir), 0.0);

    // inside a window the film pools by depth; openUV.y runs 0 at the top
    float pool = mix(1.0, 0.45, openIdx > 0.5 ? openUV.y : 0.0);
    vec3 noisePos = dir * 4.0 + vec3(0.0, 0.0, uTime * 0.15);
    float noiseVal = fbm(noisePos) * 0.5 + 0.5;
    float thickness = mix(200.0, 800.0, noiseVal * 0.7 + L * 0.3) * pool;

    float r = thinFilmReflectance(cosTheta, 650.0, thickness);
    float g = thinFilmReflectance(cosTheta, 510.0, thickness);
    float b = thinFilmReflectance(cosTheta, 475.0, thickness);

    vec3 halfVector = normalize(vec3(1.0, 1.0, 1.0) + viewDir);
    float specular = pow(max(dot(normal, halfVector), 0.0), 64.0) * 0.5;

    vec3 oilColor = vec3(r, g, b) * 1.5 + (uColor * specular * 2.0);
    oilColor = oilColor / (oilColor + vec3(1.0));

    // ---- sky on the wall, oil in the openings -----------------------------
    // this is their `useLighting` mix, driven by the mask instead of a uniform
    float oilAmount = max(mWin, mDoor * 0.6) * uSkyToOil;
    vec3 col = mix(sky, oilColor, oilAmount);

    // ---- sit ON the plate, never instead of it ----------------------------
    // screen-blend keeps the plate's dither visible through your layer
    vec3 scr = 1.0 - (1.0 - col) * (1.0 - plate * uPlateMix);
    col = mix(col, scr, uPlateMix);

    // never brighter than the hall around you
    col *= mix(0.25, 1.10, uArc);

    // ---- PSX colour depth + ordered dither --------------------------------
    if (uLevels > 1.5) {
        float dith = bayer4(gl_FragCoord.xy / max(uGrid, 1.0)) / uLevels;
        col = floor(clamp(col + dith, 0.0, 1.0) * uLevels + 0.5) / uLevels;
    }

    col *= mProj;
    fragColor = TDOutputSwizzle(vec4(col, 1.0));
}
