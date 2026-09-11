// ---------------------------------------------------------------------------
// PSX material - PIXEL shader.  TouchDesigner GLSL MAT.
//
// The other three things that make the look:
//   * nearest texture sampling, no mipmaps
//        Set this on the TOP feeding sTD2DInputs[0]:
//        Filter = Nearest, Mip Map Filter = None, Anisotropic = Off.
//        The shader cannot do it for you - it is a sampler state.
//   * reduced colour depth with ordered dither  (PSX was 15/16-bit)
//   * vertex fog, as a cheap and period-correct depth cue
//
// INPUTS
//   sTD2DInputs[0]   albedo texture (point-sampled, no mips)
//
// CUSTOM UNIFORMS
//   uLevels      float   quantisation steps per channel. 32 = 5 bits = PSX.
//   uGrid        float   dither cell size in pixels. Keep it a multiple of your
//                        render scale so the pattern locks to the pixel grid.
//                        Rendering the 3D at 1/4 and upscaling x4 -> use 4.
//   uFogColor    vec3    fog colour - match your background, near black here
//   uFogNear     float   world units where fog starts
//   uFogFar      float   world units where fog is total
//   uCutout      float   alpha-test threshold, 0 to disable
// ---------------------------------------------------------------------------

uniform float uLevels;
uniform float uGrid;
uniform vec3  uFogColor;
uniform float uFogNear;
uniform float uFogFar;
uniform float uCutout;

in Vertex {
    vec4 color;
    vec3 worldSpacePos;
    vec3 worldSpaceNorm;
    flat int cameraIndex;
} iVert;

noperspective in vec2 psxUV;

out vec4 fragColor[TD_NUM_COLOR_BUFFERS];

float bayer4(vec2 px) {
    const float m[16] = float[16](
         0.0,  8.0,  2.0, 10.0,
        12.0,  4.0, 14.0,  6.0,
         3.0, 11.0,  1.0,  9.0,
        15.0,  7.0, 13.0,  5.0);
    ivec2 c = ivec2(mod(floor(px), 4.0));
    return m[c.y * 4 + c.x] / 16.0 - 0.5;
}

void main()
{
    TDCheckDiscard();

    // affine UVs - textureLod(..., 0.0) also guarantees no mip selection
    vec4 tex = textureLod(sTD2DInputs[0], psxUV, 0.0);

    if (uCutout > 0.0 && tex.a < uCutout) discard;

    vec4 col = tex * iVert.color;

    // ---- fog ---------------------------------------------------------------
    float dist = distance(iVert.worldSpacePos, uTDMats[iVert.cameraIndex].camInverse[3].xyz);
    float fog  = clamp((dist - uFogNear) / max(uFogFar - uFogNear, 1e-4), 0.0, 1.0);
    col.rgb = mix(col.rgb, uFogColor, fog);

    // ---- PSX colour depth + ordered dither ---------------------------------
    if (uLevels > 1.5) {
        float d = bayer4(gl_FragCoord.xy / max(uGrid, 1.0)) / uLevels;
        col.rgb = floor(clamp(col.rgb + d, 0.0, 1.0) * uLevels + 0.5) / uLevels;
    }

    fragColor[0] = TDOutputSwizzle(col);
}
