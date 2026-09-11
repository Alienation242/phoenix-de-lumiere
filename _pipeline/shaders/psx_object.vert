#version 330
// PS1 object vertex shader - vertex snapping + affine UVs, standalone GL 3.3.
// The snap is your own from PS1_VERTEX_SHADER:
//     projectedPosition.xyz = floor(projectedPosition.xyz * resolution) / resolution;

in vec3 aPos;
in vec3 aNrm;
in vec2 aUv;

uniform mat4  uMVP;
uniform mat4  uModel;
uniform float uSnapRes;      // your `resolution`. lower = more wobble
uniform vec3  uLightDir;
uniform vec3  uLightCol;
uniform vec3  uAmbient;
uniform vec3  uTint;
uniform float uEmissive;

out vec3 vLit;
noperspective out vec2 vUvA;   // affine - the PS1 texture swim

void main() {
    vec4 clip = uMVP * vec4(aPos, 1.0);
    if (uSnapRes > 0.5) {
        clip.xyz = floor(clip.xyz * uSnapRes) / uSnapRes;
    }
    gl_Position = clip;

    vec3 n = normalize(mat3(uModel) * aNrm);
    float diff = max(dot(n, normalize(uLightDir)), 0.35);
    vLit = uTint * (uAmbient + uLightCol * diff) + uTint * uEmissive;
    vUvA = aUv;
}
