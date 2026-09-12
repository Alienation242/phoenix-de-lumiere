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
uniform float uIntro;
uniform float uSaturation;
uniform float uLevels;
uniform float uGrid;
uniform float uTrimLift;      // 0 = cap and plinth are the same stone as the shaft
uniform float uFlutes;        // grooves down the shaft. 0 = a plain cylinder
uniform float uFluteDepth;    // how far a groove tips the normal
uniform float uFluteAO;       // how dark a groove is regardless of the sun
uniform vec2  uSunDir;        // where the light is, in wall space: x across
uniform float uPlateMean;     // THIS frame's mean luma, straight from noise_arc.csv
uniform float uPlateHP;       // 1 = keep the plate's grain, drop the wall's banding
uniform float uPlateBlur;     // radius of "local", in canvas px
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

    // Sampling the mask shifted moves the pillar itself - which means moving
    // real architecture, and at the old 95 px that was the parallax reading as
    // wrong rather than as depth. Kept tiny now. The pillar's SHADING still
    // swings the full amount below, which is where the depth cue actually
    // comes from and costs nothing in registration.
    vec2 suv = vUv - vec2(shiftPx / uCanvasW, 0.0);
    vec4 aux = texture(uAux, suv);
    float body = aux.g, trim = aux.b;
    float solid = clamp(body + trim, 0.0, 1.0);
    if (solid < 0.004) { fragColor = vec4(0.0); return; }

    float u = columnU(suv.x * uCanvasW);
    if (u < 0.0) u = 0.5;

    // treat the column as a cylinder: the normal swings -1..1 across its width
    float nx = clamp(u * 2.0 - 1.0, -1.0, 1.0);

    // Flutes. Shaft only - cap and plinth are not fluted on a real column, and
    // `body` is exactly the shaft matte.
    //
    // Perturbing nx ALONE very nearly does nothing, which is worth writing
    // down. The shading below is driven by NoV, and with V almost head-on
    // NoV is just nz = sqrt(1 - nx*nx). That is flat near the middle of the
    // shaft, so a groove there moves the brightness by a couple of per cent
    // and only bites at the edges, which is the opposite of what carving looks
    // like. A symmetric groove under a head-on light shades symmetrically and
    // disappears.
    //
    // So the tilt goes in (it is the honest part, and it feeds the specular
    // band) AND the groove is lit from the side, using the same direction the
    // band below uses so the carving and the highlight agree about where the
    // light is.
    float fluteShade = 0.0;
    if (uFlutes >= 1.0 && uFluteDepth > 0.0) {
        float p  = u * uFlutes * 6.28318530718;
        float dn = sin(p);                          // the flank's own tilt
        // 0 on a ridge, -1 at the bottom of a groove. This is what keeps the
        // flutes visible when the sun is straight on: a real groove is shaded
        // by its own depth whatever the light does, and without it the whole
        // shaft would flatten out every time the sun crossed its axis.
        float hollow = (cos(p) - 1.0) * 0.5;
        nx = clamp(nx + dn * uFluteDepth * 0.35 * body, -1.0, 1.0);
        // The directional half. As uSunDir.x swings through zero the lit flank
        // changes sides, which is what a column does when the sun crosses it.
        fluteShade = (dn * uSunDir.x + hollow * uFluteAO) * uFluteDepth * body;
    }
    float nz = sqrt(max(0.0, 1.0 - nx * nx));
    vec3  N  = normalize(vec3(nx, 0.0, nz));
    vec3  V  = normalize(vec3(camN * 0.45, 0.0, 1.0));   // the camera really does move
    vec3  R  = reflect(-V, N);

    // A plain two-tone sky, not skyEnv: that call generates a cloud field, and
    // there are no procedural clouds anywhere on this wall any more. The plate
    // is the only texture, and it arrives via Lp below.
    float up = clamp(R.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 env = mix(uColor * 0.10, uColor * 0.42 + vec3(0.02), up) * uSkyGain;
    float NoV = max(dot(N, V), 0.0);

    // NoV is 1 down the centre of the shaft and 0 at its edges, so this alone
    // is the cylinder. It needs real range or the pillar reads as a flat slab.
    // Brighter and with far more range across the shaft than before. These are
    // meant to be lit stone standing in the room, not silhouettes.
    vec3 col = uStone * (0.30 + 1.05 * NoV) + env * 0.34;
    col *= (1.0 + fluteShade);

    // a vertical specular band that slides around the shaft as the camera moves
    float band = pow(max(dot(N, normalize(vec3(0.55 - camN * 0.8, 0.25, 0.8))), 0.0), 14.0);
    col += mix(uColor, vec3(1.0), 0.35) * band * 0.55;

    // Cap and plinth are the SAME STONE as the shaft, and by default nothing
    // here distinguishes them.
    //
    // They used to be lifted, gently in the code - mix(col, col*1.12 + stone,
    // 0.6) - and not at all gently on the wall. Measured on a rendered frame,
    // that put the cap 41 % brighter than the shaft and the plinth 15 %
    // brighter, and since the trim mattes sit at the TOP and BOTTOM of the
    // pillar, the shaft in between read as a dark band lying across it. A
    // painted-on shadow, and the second one of those to come off this shader.
    //
    // The cap and plinth are already wider than the shaft. That silhouette is
    // what says "capital" - it does not need a brightness step as well.
    if (uTrimLift > 0.0) col = mix(col, col * 1.12 + uStone * 0.05, trim * uTrimLift);

    // No contact darkening. It multiplied the silhouette down to 0.45 and drew
    // a hard dark stripe down both sides of every pillar - a painted-on shadow,
    // not a contact shadow, and the first thing you see on the wall. The
    // cylinder shading above already turns the shaft away at its edges, which
    // is the part that was doing real work.

    // The plate shades the pillars too - it is the base for everything on this
    // wall, the pillars included - but only its GRAIN, not its banding.
    //
    // The wall behind a pillar is not evenly lit: measured at one frame, the
    // plate runs at 78-86 across the band the capital sits in, 47-58 down the
    // shaft, and 58-69 again at the base. Multiplying the pillar by that
    // printed the wall's own horizontal bands onto it, and what you saw was a
    // dark shadow lying across the middle of the pillar with a bright cap and a
    // bright plinth - the wall showing through a solid object.
    //
    // Same mistake as the chrome looking transparent, and the same fix: the
    // pillar stands in the ROOM. So the plate is high-passed - its own local
    // average subtracted and the frame's mean put back - which keeps every bit
    // of the dither and takes the architecture's lighting off the object.
    float Lp = dot(texture(uPlate, vUv).rgb, vec3(0.2126, 0.7152, 0.0722));
    if (uPlateHP > 0.0) {
        vec2 r = vec2(1.0 / uCanvasW, uRes.x / (uCanvasW * uRes.y)) * uPlateBlur;
        float acc = 0.0;
        for (int j = -1; j <= 1; ++j) {
            for (int i = -1; i <= 1; ++i) {
                acc += dot(texture(uPlate, vUv + vec2(float(i), float(j)) * r).rgb,
                           vec3(0.2126, 0.7152, 0.0722));
            }
        }
        float local = acc / 9.0;
        Lp = mix(Lp, clamp(uPlateMean + (Lp - local), 0.0, 1.0), uPlateHP);
    }
    col *= mix(0.45, 1.30, Lp);
    col *= mix(0.30, 1.10, uArc) * uGain;

    col = mix(vec3(dot(col, vec3(0.2126, 0.7152, 0.0722))), col, uSaturation);
    col = quantise(col, uLevels, uGrid, gl_FragCoord.xy);
    col *= texture(uMasks, vUv).a;        // a shifted pillar must still not light black

    float a = solid * uIntro;
    fragColor = vec4(col * a, a);         // premultiplied
}
