// ---------------------------------------------------------------------------
// PSX material - VERTEX shader.  TouchDesigner GLSL MAT.
//
// Three of the six things that make the look, all of them here:
//   * vertex snapping    - the console had no sub-pixel precision, so vertices
//                          jump between screen positions. This is the single
//                          most recognisable PSX artifact.
//   * affine UVs         - no perspective-correct interpolation, so textures
//                          swim and warp on large polygons. That is what
//                          "noperspective" does below.
//   * vertex lighting    - Gouraud only, computed here, interpolated across the
//                          face. No per-pixel lighting, no shadows.
//
// CUSTOM UNIFORMS
//   uSnap        float   snap grid, in "virtual pixels" across the screen.
//                        Match your render scale: rendering at 2447x638 (1/4),
//                        try 160-320. Lower = more wobble.
//   uLightDir    vec3    world-space direction TO the light
//   uLightCol    vec3    light colour
//   uAmbient     vec3    ambient term - keep it lifted, PSX had no bounce
//   uEmissive    float   extra glow, drive this from the noise plate so the
//                        models pulse with the rest of the building
// ---------------------------------------------------------------------------

uniform float uSnap;
uniform vec3  uLightDir;
uniform vec3  uLightCol;
uniform vec3  uAmbient;
uniform float uEmissive;

out Vertex {
    vec4 color;
    vec3 worldSpacePos;
    vec3 worldSpaceNorm;
    flat int cameraIndex;
} oVert;

// affine (non perspective-correct) texture coordinates - the PSX texture swim
noperspective out vec2 psxUV;

void main()
{
    vec4 worldSpacePos = TDDeform(P);
    vec4 clip = TDWorldToProj(worldSpacePos);

    // ---- vertex snapping ---------------------------------------------------
    // Quantise in NDC, then re-apply w. Guard against w <= 0 (vertices behind
    // the camera) or the geometry explodes across the frame.
    if (uSnap > 0.5 && clip.w > 1e-4) {
        vec2 grid = vec2(uSnap) * 0.5;
        vec3 ndc = clip.xyz / clip.w;
        ndc.xy = floor(ndc.xy * grid + 0.5) / grid;
        clip.xyz = ndc * clip.w;
    }
    gl_Position = clip;

    // ---- Gouraud lighting --------------------------------------------------
    vec3 n = normalize(TDDeformNorm(N));
    float ndl = max(dot(n, normalize(uLightDir)), 0.0);
    vec4 cd = TDInstanceColor(Cd);
    vec3 lit = cd.rgb * (uAmbient + uLightCol * ndl) + uEmissive * cd.rgb;

    oVert.color         = vec4(lit, cd.a);
    oVert.worldSpacePos = worldSpacePos.xyz;
    oVert.worldSpaceNorm = n;
    oVert.cameraIndex   = TDCameraIndex();

    psxUV = uv[0].st;
}
