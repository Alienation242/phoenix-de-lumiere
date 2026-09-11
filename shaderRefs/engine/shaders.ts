export const PS1_VERTEX_SHADER = `
  uniform float resolution;
  varying vec2 vUv;
  varying vec3 vPosition;
  varying vec3 vNormal;
  void main() {
    vUv = uv;
    vPosition = (modelMatrix * vec4(position, 1.0)).xyz;
    vNormal = normalMatrix * normal;
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vec4 projectedPosition = projectionMatrix * mvPosition;
    projectedPosition.xyz = floor(projectedPosition.xyz * resolution) / resolution;
    gl_Position = projectedPosition;
  }
`;

export const PS1_LANDSCAPE_VERTEX = `
  uniform float resolution;
  uniform float displacementAmount;
  varying vec2 vUv;
  varying vec3 vPosition;
  varying vec3 vNormal;

  void main() {
    vUv = uv;
    vec3 pos = position;

    float height = sin(pos.x * 0.04) * cos(pos.y * 0.04) * 3.0;
    height += sin(pos.x * 0.015 + pos.y * 0.02) * 5.0;

    float stepSize = 0.8;
    float steppedHeight = floor(height / stepSize) * stepSize;
    pos.z += steppedHeight * displacementAmount;

    vNormal = normalize(vec3(sin(pos.x * 0.05) * 0.5, 1.0, cos(pos.y * 0.05) * 0.5));

    vPosition = (modelMatrix * vec4(pos, 1.0)).xyz;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    vec4 projectedPosition = projectionMatrix * mvPosition;
    projectedPosition.xyz = floor(projectedPosition.xyz * resolution) / resolution;
    gl_Position = projectedPosition;
  }
`;

export const PS1_FRAGMENT_SOLID = `
  uniform vec3 color;
  uniform float useLighting;
  uniform float useNormalMap;
  uniform float time;
  uniform samplerCube envMap;
  varying vec3 vNormal;
  varying vec3 vPosition;
  varying vec2 vUv;

  void main() {
    vec3 normal = normalize(vNormal);

    vec3 lightDir = normalize(vec3(1.0, 1.0, 0.5));
    float diff = max(dot(normal, lightDir), 0.4);
    vec3 litColor = mix(color, color * diff * 1.5, 1.0);
    vec3 nColorFlat = normal * 0.5 + 0.5;
    vec3 baseColor = mix(litColor, nColorFlat, useNormalMap);

    vec3 viewDir = normalize(vPosition - cameraPosition);
    vec3 reflectDir = reflect(viewDir, normal);
    vec3 reflection = textureCube(envMap, reflectDir).rgb;

    float fresnel = pow(1.0 - max(dot(normal, -viewDir), 0.0), 3.0);
    float pulse = 0.85 + 0.15 * sin(time * 1.6);

    vec3 chromeTint = mix(color * 0.5, vec3(1.0), fresnel * 0.7);
    vec3 chromeColor = reflection * mix(vec3(0.75), vec3(1.4), fresnel) * pulse + chromeTint * 0.12;

    vec3 halfVector = normalize(lightDir - viewDir);
    float specular = pow(max(dot(normal, halfVector), 0.0), 64.0);
    chromeColor += vec3(specular * 0.7);

    vec3 finalColor = mix(baseColor, chromeColor, useLighting);

    gl_FragColor = vec4(finalColor, 1.0);
  }
`;

export const PS1_PILLAR_FRAGMENT = `
  uniform vec3 color;
  uniform float opacity;
  uniform float useLighting;
  uniform vec3 fogColor;
  uniform float fogMinDist;
  uniform float fogMaxDist;
  varying vec3 vPosition;
  varying vec3 vNormal;

  void main() {
    vec3 nColor = normalize(vNormal) * 0.5 + 0.5;

    vec3 viewDir = normalize(cameraPosition - vPosition);
    float fresnel = pow(1.0 - max(dot(normalize(vNormal), viewDir), 0.0), 2.5);
    vec3 glassColor = mix(color * 0.1, vec3(1.0), fresnel);

    vec3 finalColor = mix(nColor, glassColor, useLighting);

    float finalOpacity = mix(opacity, opacity * mix(0.4, 0.9, fresnel), useLighting);

    float dist = distance(cameraPosition, vPosition);
    float fogFactor = 1.0 - smoothstep(fogMinDist, fogMaxDist, dist);
    finalColor = mix(fogColor, finalColor, fogFactor);

    gl_FragColor = vec4(finalColor, finalOpacity);
  }
`;

export const PS1_FRAGMENT_WIREFRAME = `
  uniform vec3 color;
  uniform float opacity;
  void main() {
    gl_FragColor = vec4(color, opacity);
  }
`;

export const PS1_LANDSCAPE_FRAGMENT = `
  uniform vec3 color;
  uniform float revealProgress;
  uniform float isSolid;
  uniform float useLighting;
  uniform sampler2D groundTexture;
  uniform vec3 fogColor;
  uniform float fogMinDist;
  uniform float fogMaxDist;
  varying vec3 vPosition;
  varying vec3 vNormal;

  float rand(vec2 co){
      return fract(sin(dot(co.xy ,vec2(12.9898,78.233))) * 43758.5453);
  }

  void main() {
    if (revealProgress <= 0.01) discard;

    float dist = length(vPosition.xz);
    float noise = rand(floor(vPosition.xz * 1.5));
    float threshold = revealProgress * 250.0;

    if (dist + noise * 12.0 > threshold) {
      discard;
    }

    float centerFade = max(0.0, 1.0 - (dist / 140.0));

    float gridX = step(0.99, fract(vPosition.x / 2.0));
    float gridZ = step(0.99, fract(vPosition.z / 2.0));
    float gridDiag = step(0.99, fract((vPosition.x - vPosition.z) / 2.0));
    float gridLine = max(max(gridX, gridZ), gridDiag) * (1.0 - isSolid);

    vec3 nColor = normalize(vNormal) * 0.5 + 0.5;
    vec4 texColor = texture2D(groundTexture, vPosition.xz * 0.03);
    vec3 solidColor = mix(nColor, texColor.rgb * 2.5, useLighting);

    vec3 finalColor = mix(color * gridLine, solidColor, isSolid) * centerFade;

    float alpha = mix(gridLine, 1.0, isSolid);
    if (alpha < 0.01) discard;

    float fogDist = distance(cameraPosition, vPosition);
    float fogFactor = 1.0 - smoothstep(fogMinDist, fogMaxDist, fogDist);
    finalColor = mix(fogColor, finalColor, fogFactor);

    gl_FragColor = vec4(finalColor, alpha);
  }
`;

export const PS1_SKY_FRAGMENT = `
  uniform vec3 color;
  uniform float opacity;
  uniform float time;
  uniform float useLighting;
  varying vec3 vPosition;
  varying vec3 vNormal;

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

  float fresnel(float cosTheta, float n1, float n2) {
      float r0 = (n1 - n2) / (n1 + n2);
      r0 *= r0;
      return r0 + (1.0 - r0) * pow(1.0 - cosTheta, 5.0);
  }

  float thinFilmReflectance(float cosTheta1, float wavelength, float thickness) {
      float uAirIOR = 1.00;
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

  void main() {
    vec3 dir = normalize(vPosition);

    vec3 zenith = color * 0.35 + vec3(0.01, 0.015, 0.02);
    vec3 horizon = color * 0.12 + vec3(0.015, 0.02, 0.03);

    float h = clamp(dir.y * 0.5 + 0.5, 0.0, 1.0);
    h = floor(h * 12.0) / 12.0;
    vec3 baseSkyColor = mix(horizon, zenith, h);

    vec3 viewDir = normalize(cameraPosition - vPosition);

    vec3 normal = -dir;
    float cosTheta = max(dot(normal, viewDir), 0.0);

    vec3 noisePos = dir * 4.0 + vec3(0.0, 0.0, time * 0.15);
    float noiseVal = fbm(noisePos) * 0.5 + 0.5;

    float thickness = mix(200.0, 800.0, noiseVal);

    float r = thinFilmReflectance(cosTheta, 650.0, thickness);
    float g = thinFilmReflectance(cosTheta, 510.0, thickness);
    float b = thinFilmReflectance(cosTheta, 475.0, thickness);

    vec3 halfVector = normalize(vec3(1.0, 1.0, 1.0) + viewDir);
    float specAngle = max(dot(normal, halfVector), 0.0);
    float specular = pow(specAngle, 64.0) * 0.5;

    vec3 oilColor = vec3(r, g, b) * 1.5 + (color * specular * 2.0);
    oilColor = oilColor / (oilColor + vec3(1.0));

    vec3 finalColor = mix(baseSkyColor, oilColor, useLighting);

    gl_FragColor = vec4(finalColor, opacity);
  }
`;

export const PS1_LIGHT_POINTS_VERTEX = `
  uniform vec2 mouseNDC;
  uniform float aspect;
  uniform float baseSize;
  uniform float time;
  uniform float globalOpacity;
  uniform float fogMinDist;
  uniform float fogMaxDist;
  attribute float twinkleSeed;
  varying float vGlow;

  void main() {
    vec3 worldPos = (modelMatrix * vec4(position, 1.0)).xyz;
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vec4 clipPosition = projectionMatrix * mvPosition;

    vec2 ndc = clipPosition.xy / clipPosition.w;
    vec2 ndcAdjusted = vec2(ndc.x * aspect, ndc.y);
    vec2 mouseAdjusted = vec2(mouseNDC.x * aspect, mouseNDC.y);
    float distToMouse = distance(ndcAdjusted, mouseAdjusted);
    float proximity = 1.0 - smoothstep(0.0, 0.4, distToMouse);

    float twinkle = 0.92 + 0.08 * sin(time * 1.6 + twinkleSeed * 6.2831);

    float fogDist = distance(cameraPosition, worldPos);
    float fogRaw = 1.0 - smoothstep(fogMinDist, fogMaxDist, fogDist);
    float fogFactor = mix(0.3, 1.0, fogRaw);

    float baseGlow = 0.6;
    float proximityGlow = proximity * 1.3;
    vGlow = (baseGlow + proximityGlow) * twinkle * globalOpacity * fogFactor;

    gl_Position = clipPosition;
    float sizeAttenuation = clamp(300.0 / -mvPosition.z, 0.5, 3.0);
    gl_PointSize = clamp(baseSize * sizeAttenuation * (1.0 + proximity * 0.9), 2.0, 55.0);
  }
`;

export const PS1_LIGHT_POINTS_FRAGMENT = `
  uniform vec3 color;
  varying float vGlow;

  void main() {
    vec2 uv = gl_PointCoord - vec2(0.5);
    float d = length(uv) * 2.0;
    float core = smoothstep(0.6, 0.0, d);
    float halo = smoothstep(1.0, 0.0, d) * 0.4;
    float mask = core + halo;

    vec3 finalColor = color * vGlow * (core * 1.3 + halo);
    float alpha = mask * clamp(vGlow, 0.0, 1.4);

    gl_FragColor = vec4(finalColor, alpha);
  }
`;
