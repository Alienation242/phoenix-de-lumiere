#version 330
// PS1 object fragment shader - flat colour, reduced depth, ordered dither.
// Writes premultiplied colour so the compositor can screen or multiply it.

in vec3 vLit;
noperspective in vec2 vUvA;
out vec4 fragColor;

uniform float uAlpha;     // 0..1, fades as the object leaves through a door
uniform float uLevels;    // colour steps per channel (32 = PSX)
uniform float uGrid;      // dither cell in px

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
    // A slight per-face gradient along the affine UV. On untextured PS1
    // geometry this is what makes the individual facets read.
    // (With an orthographic camera w is constant, so `noperspective` costs
    //  nothing and changes nothing - it matters the moment you go perspective.)
    vec3 col = vLit * (0.90 + 0.10 * vUvA.y);
    if (uLevels > 1.5) {
        float d = bayer4(gl_FragCoord.xy / max(uGrid, 1.0)) / uLevels;
        col = floor(clamp(col + d, 0.0, 1.0) * uLevels + 0.5) / uLevels;
    }
    fragColor = vec4(col * uAlpha, uAlpha);   // premultiplied
}
