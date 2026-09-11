// ---------------------------------------------------------------------------
// Shared GLSL for the SW wall. #include'd by render_shader.py (it resolves
// #include itself - core GL 3.3 has no preprocessor for it).
//
// hash / noise / fbm / fresnel / thinFilmReflectance are ported verbatim from
// shaderRefs/engine/shaders.ts (PS1_SKY_FRAGMENT). skyEnv() wraps their banded
// sky so the wall and the chrome objects reflect ONE world: whatever the wall
// shows is what you see in the metal.
// ---------------------------------------------------------------------------
#ifndef LIB_COMMON
#define LIB_COMMON

#define PI 3.14159265359

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

// fbm with more octaves - only for the chrome, where the reflection is
// magnified and 5 octaves show their largest lobe as an obvious blob.
float fbmHi(vec3 p) {
    float value = 0.0;
    float amplitude = 0.5;
    float frequency = 2.0;
    for (int i = 0; i < 7; i++) {
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

// ---------------------------------------------------------------------------
// The world, as one function.
//
// `dir` is a direction in world space. The wall calls it with the direction its
// own pixel looks along; a chrome object calls it with the reflected view ray.
// `soft` (0..1) blurs the banding out for rough/curved reflections - a mirror
// ball must not show the sky's 12 hard steps at full contrast or it reads as a
// cardboard cutout rather than metal.
// ---------------------------------------------------------------------------
vec3 skyEnv(vec3 dir, float t, float bands, float cloud, float cloudSpeed,
            vec3 colour, float gain, float soft) {
    vec3 zenith  = colour * 0.35 + vec3(0.01, 0.015, 0.02);
    vec3 horizon = colour * 0.12 + vec3(0.015, 0.02, 0.03);

    float h = clamp(dir.y * 0.5 + 0.5, 0.0, 1.0);
    float hb = floor(h * bands) / bands;
    h = mix(hb, h, soft);                      // soft = 1 dissolves the steps
    vec3 base = mix(horizon, zenith, h) * gain;

    vec3 cp = dir * 3.0 + vec3(t * cloudSpeed, 0.0, t * cloudSpeed * 0.4);
    float cd = fbm(cp) * 0.5 + 0.5;
    float cover = smoothstep(1.0 - cloud, 1.25 - cloud, cd);
    float cb = floor(cover * bands) / bands;
    cover = mix(cb, cover, soft);

    float lift = fbm(cp + vec3(0.0, 0.35, 0.0)) * 0.5 + 0.5;
    vec3 cloudCol = mix(vec3(0.42, 0.46, 0.55), vec3(1.0, 0.97, 0.92), lift);

    return mix(base, cloudCol * 0.88, cover * 0.85);
}

// Ordered dither. uGrid is the cell in RENDER pixels and must never be tied to
// --div, or a quarter-scale preview stops predicting the full-res render.
float bayer4(vec2 px) {
    const float m[16] = float[16](
         0.0,  8.0,  2.0, 10.0,
        12.0,  4.0, 14.0,  6.0,
         3.0, 11.0,  1.0,  9.0,
        15.0,  7.0, 13.0,  5.0);
    ivec2 c = ivec2(mod(floor(px), 4.0));
    return m[c.y * 4 + c.x] / 16.0 - 0.5;
}

vec3 quantise(vec3 col, float levels, float grid, vec2 fragPx) {
    if (levels <= 1.5) return col;
    float d = bayer4(fragPx / max(grid, 1.0)) / levels;
    return floor(clamp(col + d, 0.0, 1.0) * levels + 0.5) / levels;
}

#endif
