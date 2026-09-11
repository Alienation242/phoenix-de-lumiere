#version 330
// ---------------------------------------------------------------------------
// The two column bays, lifted off the wall and made into pillars that stand in
// FRONT of everything else.
//
// They are the nearest layer, so they carry the most parallax, and the objects
// pass BEHIND them. That occlusion is what makes the depth read: a shape
// disappearing behind a nearer shape is a far stronger cue than any amount of
// shading, which is why the objects are composited under this pass.
//
// The silhouette shift is kept deliberately small. The mask is real
// architecture, and a projected pillar that wanders far off its physical column
// misregisters visibly on the wall. The SHADING swings much further than the
// outline does - that buys the movement without the risk.
// ---------------------------------------------------------------------------
#include "lib_common.glsl"

in  vec2 vUv;
out vec4 fragColor;

uniform sampler2D uAux;       // R sdf, G column, B trim, A openings
uniform sampler2D uPlate;
uniform sampler2D uMasks;     // A = projectable

uniform vec2  uRes;
uniform float uTime;
uniform float uArc;
uniform float uCamX;
uniform float uCamAmp;
uniform float uShift;         // silhouette shift in canvas px at |camN| = 1
uniform float uWobble;        // slow bend, in canvas px
uniform float uBands;
uniform float uCloud;
uniform float uCloudSpeed;
uniform vec3  uColor;
uniform vec3  uStone;         // warm, so the pillars read as objects in the room
                              // rather than another shade of the sky behind them
uniform float uSkyGain;
uniform float uGain;
uniform float uLevels;
uniform float uGrid;
uniform vec4  uCol0;          // x, y, w, h of column body 1, in canvas px
uniform vec4  uCol1;
uniform float uCanvasW;

// local 0..1 across whichever column this pixel belongs to, or -1 outside both
float columnU(float xPx) {
    if (xPx >= uCol0.x && xPx <= uCol0.x + uCol0.z) return (xPx - uCol0.x) / uCol0.z;
    if (xPx >= uCol1.x && xPx <= uCol1.x + uCol1.z) return (xPx - uCol1.x) / uCol1.z;
    return -1.0;
}

void main() {
    float camN = uCamX / max(uCamAmp, 1e-3);

    // a slow bend down the height, so the pillars are not dead straight
    float bend = sin(uTime * 0.31 + vUv.y * 2.3) * uWobble
               + sin(uTime * 0.17 + vUv.y * 0.9) * uWobble * 0.6;
    float shiftPx = camN * uShift + bend;

    // sampling the mask shifted is what moves the pillar itself
    vec2 suv = vUv - vec2(shiftPx / uCanvasW, 0.0);
    vec4 aux = texture(uAux, suv);
    float body = aux.g, trim = aux.b;
    float solid = clamp(body + trim, 0.0, 1.0);
    if (solid < 0.004) { fragColor = vec4(0.0); return; }

    float u = columnU(suv.x * uCanvasW);
    if (u < 0.0) u = 0.5;

    // treat the column as a cylinder: the normal swings -1..1 across its width
    float nx = clamp(u * 2.0 - 1.0, -1.0, 1.0);
    float nz = sqrt(max(0.0, 1.0 - nx * nx));
    vec3  N  = normalize(vec3(nx, 0.0, nz));
    vec3  V  = normalize(vec3(camN * 0.45, 0.0, 1.0));   // the camera really does move
    vec3  R  = reflect(-V, N);

    // stone that picks the sky up rather than mirroring it
    vec3 env = skyEnv(R, uTime, uBands, uCloud, uCloudSpeed, uColor, uSkyGain, 0.85);
    float NoV = max(dot(N, V), 0.0);

    // NoV is 1 down the centre of the shaft and 0 at its edges, so this alone
    // is the cylinder. It needs real range or the pillar reads as a flat slab.
    vec3 col = uStone * (0.13 + 0.80 * NoV) + env * 0.20;

    // a vertical specular band that slides around the shaft as the camera moves
    float band = pow(max(dot(N, normalize(vec3(0.55 - camN * 0.8, 0.25, 0.8))), 0.0), 14.0);
    col += mix(uColor, vec3(1.0), 0.35) * band * 0.55;

    // cap and plinth are brighter and flatter than the shaft
    col = mix(col, col * 1.30 + uStone * 0.10, trim * (1.0 - body));

    // contact darkening at the silhouette, so the pillar bites against the wall
    float edge = smoothstep(0.0, 0.10, min(u, 1.0 - u));
    col *= mix(0.45, 1.0, edge);

    float Lp = dot(texture(uPlate, vUv).rgb, vec3(0.2126, 0.7152, 0.0722));
    col *= mix(0.65, 1.15, Lp);
    col *= mix(0.30, 1.10, uArc) * uGain;

    col = quantise(col, uLevels, uGrid, gl_FragCoord.xy);
    col *= texture(uMasks, vUv).a;        // a shifted pillar must still not light black

    float a = solid;
    fragColor = vec4(col * a, a);         // premultiplied
}
