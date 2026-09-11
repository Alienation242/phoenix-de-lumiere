#version 330
// ---------------------------------------------------------------------------
// Chrome. A polished metal shell reflecting the same world the wall is showing.
//
// Metal has no diffuse term - everything you see in chrome is the environment,
// bent by the surface normal and weighted by fresnel. That is why reusing
// skyEnv() matters: the objects reflect THIS venue's sky, with THIS venue's
// clouds moving in it, so they read as physically present in the room rather
// than pasted on top of it.
//
// Two things are doing most of the work here, and both are easy to get wrong:
//
//  1. THE VIEW RAY VARIES ACROSS THE SCREEN. The camera is orthographic, so the
//     obvious V = (0,0,1) is constant - and a constant V over a flat face gives
//     a constant normal, a constant reflection, and a cube that renders as a
//     flat grey card. uViewFov fakes just enough perspective divergence to put
//     a gradient back across every flat face.
//
//  2. THERE IS A HORIZON. Chrome reads as chrome because it shows a hard
//     light/dark split that swings as the object turns. A uniform sky gives you
//     a pearl, not a mirror. envChrome() puts a dark floor under the sky, a hot
//     line exactly at the join, and a small fierce sun.
// ---------------------------------------------------------------------------
#include "lib_common.glsl"

in vec3 vNrm;
in vec3 vWorld;
out vec4 fragColor;

uniform vec2  uRes;
uniform sampler2D uPlate;    // the room itself, as ambient light on the metal

uniform float uTime;
uniform float uBands;
uniform float uCloud;
uniform float uCloudSpeed;
uniform vec3  uColor;
uniform float uSkyGain;

uniform vec3  uTint;         // metal colour. near-white = chrome
uniform float uGloss;        // specular exponent. high = a tight, hard highlight
uniform float uSpecGain;
uniform float uRoomMix;      // how much of the wall's brightness the metal picks up
uniform vec3  uLightDir;
uniform vec3  uLightCol;
uniform float uAlpha;
uniform float uExposure;
uniform float uViewFov;      // fake perspective divergence, see note 1 above
uniform float uHorizonHot;   // brightness of the reflected horizon line
uniform float uWindows;      // brightness of the room openings, reflected

// Filmic curve. Chrome generates values far above 1.0, and a hard clamp turns
// every highlight into a flat white blob with a visible edge - the clearest
// single tell of a fake render.
vec3 tonemap(vec3 x) {
    x *= uExposure;
    vec3 a = x * (2.51 * x + 0.03);
    vec3 b = x * (2.43 * x + 0.59) + 0.14;
    return clamp(a / b, 0.0, 1.0);
}

vec3 envChrome(vec3 d, float soft) {
    // A mirror needs RANGE, not brightness. The sky goes up well past 1.0 and
    // the ground goes almost to black; the filmic curve pulls the top back down
    // afterwards. Compress this and the metal turns into a pearl - lots of
    // light, no contrast, no reflection to read.
    vec3 sky = skyEnv(d, uTime, uBands, uCloud, uCloudSpeed, uColor, uSkyGain, soft) * 2.3;
    vec3 ground = uColor * 0.020 + vec3(0.004, 0.005, 0.007);

    // extra high-frequency cloud, only in the reflection. The wall reads its
    // clouds at 1:1 but a mirror magnifies them, and 5 octaves show their
    // largest lobe as one obvious blob.
    float det = fbmHi(d * 7.0 + vec3(uTime * 0.06, 0.0, 0.0)) * 0.5 + 0.5;
    sky *= 0.72 + 0.56 * det;

    float h = smoothstep(-0.035, 0.035, d.y);
    vec3 e = mix(ground, sky, h);

    // ---- the room, reflected -------------------------------------------
    // A mirrored ball in a smooth gradient reflects a smooth gradient, and that
    // is the whole reason a chrome sphere can look boring: there is nothing in
    // the environment for the curvature to bend. Real chrome renders are sold
    // by hard-edged bright shapes - studio strip lights, or in this case the
    // venue's own rhythm of tall openings, wrapped around the horizon.
    float az = atan(d.z, d.x);
    float cell = abs(fract(az * (9.0 / PI)) - 0.5) * 2.0;
    float bar = smoothstep(0.70, 0.34, cell);

    float above = smoothstep(-0.02, 0.10, d.y) * smoothstep(0.66, 0.30, d.y);
    e += vec3(1.0, 0.985, 0.95) * bar * above * uWindows;

    // and the same openings again in the floor, which is what a polished floor
    // does and what gives the lower hemisphere something to show
    float below = smoothstep(-0.62, -0.36, d.y) * (1.0 - smoothstep(-0.22, -0.05, d.y));
    e += vec3(0.80, 0.85, 1.0) * bar * below * uWindows * 0.40;

    // the hot line at the horizon, the single strongest "this is metal" cue
    e += vec3(1.0, 0.98, 0.94) * exp(-abs(d.y) * 34.0) * uHorizonHot;

    // two suns: a tight core and a broader flare. One alone is a pinprick on a
    // sphere this size.
    float sd = max(dot(d, normalize(uLightDir)), 0.0);
    e += uLightCol * pow(sd, 900.0) * 8.0;
    e += uLightCol * pow(sd, 60.0) * 0.9;

    return e;
}

void main() {
    vec3 N = normalize(vNrm);

    // fake just enough perspective that flat faces stop being flat colour
    vec2 ndc = (gl_FragCoord.xy / uRes) * 2.0 - 1.0;
    float aspect = uRes.x / uRes.y;
    vec3 V = normalize(vec3(ndc.x * uViewFov * aspect, ndc.y * uViewFov, 1.0));

    vec3 R = reflect(-V, N);
    float NoV = max(dot(N, V), 1e-4);

    // chrome F0 is high and slightly tinted; fresnel takes it to white at grazing
    vec3 F0 = uTint * 0.92 + vec3(0.08);
    vec3 F  = F0 + (1.0 - F0) * pow(1.0 - NoV, 5.0);

    // two samples at different sharpness: the blurred one is the body of the
    // reflection, the sharp one keeps the horizon and the sun crisp
    vec3 env = mix(envChrome(R, 0.45), envChrome(R, 0.04), 0.60);

    vec3 col = env * F;

    // the room it is actually flying through
    vec3 room = texture(uPlate, gl_FragCoord.xy / uRes).rgb;
    col += room * uRoomMix * F;

    // one hard key light - the highlight that sells "polished"
    vec3 L = normalize(uLightDir);
    vec3 H = normalize(L + V);
    float spec = pow(max(dot(N, H), 0.0), uGloss);
    col += uLightCol * spec * uSpecGain;

    // grazing-angle rim, the bright outline around a real metal object
    float rim = pow(1.0 - NoV, 3.5);
    col += uColor * rim * 0.35;

    col = tonemap(col);
    fragColor = vec4(col * uAlpha, uAlpha);   // premultiplied
}
