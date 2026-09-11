#version 330
// Chrome object vertex shader.
//
// Deliberately NOT psx_object.vert: no vertex snapping, no affine UVs, smooth
// interpolated normals. The PS1 treatment stays on the wall; the objects that
// travel through it are clean, and the contrast between the two is the point.

in vec3 aPos;
in vec3 aNrm;
// no aUv: chrome needs no texture coordinates, and an attribute the fragment
// stage never reads is optimised away by the driver, so binding it by name
// fails at VAO creation. The UVs stay in the buffer (skipped with 2x4) so one
// vertex format still feeds both these shaders and the PSX ones.

uniform mat4 uMVP;
uniform mat4 uModel;
uniform mat3 uNormalMat;   // inverse-transpose, so scale cannot shear the normals

out vec3 vNrm;
out vec3 vWorld;

void main() {
    vec4 world = uModel * vec4(aPos, 1.0);
    vWorld = world.xyz;
    vNrm   = normalize(uNormalMat * aNrm);
    gl_Position = uMVP * vec4(aPos, 1.0);
}
