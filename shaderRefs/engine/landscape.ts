import * as THREE from 'three';
import {
  PS1_VERTEX_SHADER,
  PS1_LANDSCAPE_VERTEX,
  PS1_FRAGMENT_SOLID,
  PS1_LANDSCAPE_FRAGMENT,
  PS1_SKY_FRAGMENT,
  PS1_PILLAR_FRAGMENT,
  PS1_LIGHT_POINTS_VERTEX,
  PS1_LIGHT_POINTS_FRAGMENT,
} from './shaders';

export class LandscapeManager {
  private group: THREE.Group = new THREE.Group();
  private core!: THREE.Mesh;
  private gridSolid!: THREE.Mesh;
  private skysphere!: THREE.Mesh;
  private pillarMeshes: THREE.Mesh[] = [];

  private coreMaterial!: THREE.ShaderMaterial;
  private solidMaterial!: THREE.ShaderMaterial;
  private skyMaterial!: THREE.ShaderMaterial;

  private streetLightMaterial!: THREE.ShaderMaterial;
  private streetLightPoints!: THREE.Points;
  private streetLightCount = 25000;
  private streetLightBaseX: Float32Array = new Float32Array(0);
  private streetLightBaseZ: Float32Array = new Float32Array(0);

  private shapes: THREE.BufferGeometry[] = [];

  private currentLayer = 0;

  private revealProgress = 0;
  private targetReveal = 0;

  private solidState = 0;
  private targetSolid = 0;

  private skyOpacity = 0;
  private targetSky = 0;

  private lightState = 0;
  private targetLight = 0;

  private normalMapState = 0;
  private targetNormalMap = 0;

  private displacementAmount = 0;
  private targetDisplacement = 0;

  private fogEnabled = 0;
  private targetFog = 0;

  private particleLightsState = 0;
  private targetParticleLights = 0;

  private currentResolution = 90.0;
  private targetResolution = 90.0;

  private fogUniforms = {
    fogColor: { value: new THREE.Color(0x8899aa) },
    fogMinDist: { value: 6.0 },
    fogMaxDist: { value: 45.0 },
  };

  public getGroup(): THREE.Group {
    return this.group;
  }

  public getCore(): THREE.Mesh {
    return this.core;
  }

  public getTerrainMesh(): THREE.Mesh {
    return this.gridSolid;
  }

  public setFog(minDist: number, maxDist: number, color: number): void {
    this.fogUniforms.fogMinDist.value = minDist;
    this.fogUniforms.fogMaxDist.value = maxDist;
    this.fogUniforms.fogColor.value.setHex(color);
  }

  public setMouseNDC(x: number, y: number): void {
    if (this.streetLightMaterial) {
      this.streetLightMaterial.uniforms['mouseNDC'].value.set(x, y);
    }
  }

  public setAspect(aspect: number): void {
    if (this.streetLightMaterial) {
      this.streetLightMaterial.uniforms['aspect'].value = aspect;
    }
  }

  public setEnvMap(texture: THREE.CubeTexture): void {
    this.coreMaterial.uniforms['envMap'].value = texture;
  }

  private terrainHeightAt(worldX: number, worldZ: number): number {
    const localX = worldX;
    const localY = -worldZ;
    let height = Math.sin(localX * 0.04) * Math.cos(localY * 0.04) * 3.0;
    height += Math.sin(localX * 0.015 + localY * 0.02) * 5.0;
    return height;
  }

  public initialize(): void {
    const textureLoader = new THREE.TextureLoader();

    const groundTex = textureLoader.load('images/ground_texture.jpg');
    groundTex.wrapS = THREE.RepeatWrapping;
    groundTex.wrapT = THREE.RepeatWrapping;
    groundTex.colorSpace = THREE.SRGBColorSpace;

    this.coreMaterial = new THREE.ShaderMaterial({
      uniforms: {
        resolution: { value: 90.0 },
        color: { value: new THREE.Color(0x44ff44) },
        useLighting: { value: 0.0 },
        useNormalMap: { value: 0.0 },
        time: { value: 0.0 },
        envMap: { value: null },
      },
      vertexShader: PS1_VERTEX_SHADER,
      fragmentShader: PS1_FRAGMENT_SOLID,
      wireframe: true,
    });

    this.solidMaterial = new THREE.ShaderMaterial({
      uniforms: {
        resolution: { value: 90.0 },
        color: { value: new THREE.Color(0x003300) },
        revealProgress: { value: 0.0 },
        isSolid: { value: 0.0 },
        useLighting: { value: 0.0 },
        displacementAmount: { value: 0.0 },
        groundTexture: { value: groundTex },
        fogColor: this.fogUniforms.fogColor,
        fogMinDist: this.fogUniforms.fogMinDist,
        fogMaxDist: this.fogUniforms.fogMaxDist,
      },
      vertexShader: PS1_LANDSCAPE_VERTEX,
      fragmentShader: PS1_LANDSCAPE_FRAGMENT,
      transparent: true,
    });

    this.skyMaterial = new THREE.ShaderMaterial({
      uniforms: {
        resolution: { value: 90.0 },
        color: { value: new THREE.Color(0x003300) },
        opacity: { value: 0.0 },
        time: { value: 0.0 },
        useLighting: { value: 0.0 },
      },
      vertexShader: PS1_VERTEX_SHADER,
      fragmentShader: PS1_SKY_FRAGMENT,
      side: THREE.BackSide,
      transparent: true,
    });

    this.shapes = [
      new THREE.TetrahedronGeometry(3.5, 0),
      new THREE.IcosahedronGeometry(3.2, 3),
      new THREE.OctahedronGeometry(3.5, 0),
      new THREE.TorusKnotGeometry(2, 0.6, 256, 32),
    ];

    this.core = new THREE.Mesh(this.shapes[0], this.coreMaterial);
    this.group.add(this.core);

    const gridGeo = new THREE.PlaneGeometry(300, 300, 40, 40);
    this.gridSolid = new THREE.Mesh(gridGeo, this.solidMaterial);
    this.gridSolid.rotation.x = -Math.PI / 2;
    this.gridSolid.position.y = -5.0;
    this.group.add(this.gridSolid);

    const skyGeo = new THREE.SphereGeometry(120, 16, 16);
    this.skysphere = new THREE.Mesh(skyGeo, this.skyMaterial);
    this.group.add(this.skysphere);

    const ringDefs = [
      { count: 24, radius: 18 },
      { count: 32, radius: 32 },
      { count: 36, radius: 50 },
      { count: 32, radius: 65 },
      { count: 40, radius: 85 },
      { count: 48, radius: 110 },
    ];

    const pillarGeo = new THREE.BoxGeometry(1.5, 30, 1.5);

    for (let ringIdx = 0; ringIdx < ringDefs.length; ringIdx++) {
      const def = ringDefs[ringIdx];
      const baseMat = new THREE.ShaderMaterial({
        uniforms: {
          resolution: { value: 90.0 },
          color: { value: new THREE.Color(0x003300) },
          opacity: { value: 0.0 },
          useLighting: { value: 0.0 },
          fogColor: this.fogUniforms.fogColor,
          fogMinDist: this.fogUniforms.fogMinDist,
          fogMaxDist: this.fogUniforms.fogMaxDist,
        },
        vertexShader: PS1_VERTEX_SHADER,
        fragmentShader: PS1_PILLAR_FRAGMENT,
        wireframe: false,
        transparent: true,
        depthWrite: true,
      });

      for (let i = 0; i < def.count; i++) {
        const angle = (i / def.count) * Math.PI * 2;
        const mat = baseMat.clone();
        const pillar = new THREE.Mesh(pillarGeo, mat);
        pillar.userData['ringIndex'] = ringIdx;
        pillar.userData['progress'] = 0;
        pillar.userData['targetProgress'] = 0;
        pillar.userData['growthSpeed'] = 0.7 + Math.random() * 0.6;
        pillar.userData['opacityProgress'] = 0;
        pillar.position.set(Math.cos(angle) * def.radius, -35.0, Math.sin(angle) * def.radius);
        pillar.rotation.y = (i / def.count) * Math.PI;
        this.group.add(pillar);
        this.pillarMeshes.push(pillar);
      }
    }

    this.createStreetLights();

    this.group.frustumCulled = false;
  }

  private createStreetLights(): void {
    const targetCount = this.streetLightCount;
    let spawnedCount = 0;

    const tempPositions: number[] = [];
    const tempSeeds: number[] = [];
    const tempBaseX: number[] = [];
    const tempBaseZ: number[] = [];

    const addLight = (x: number, z: number) => {
      if (spawnedCount >= targetCount) return;

      tempPositions.push(x, -4.6, z);
      tempBaseX.push(x);
      tempBaseZ.push(z);
      tempSeeds.push(Math.random());
      spawnedCount++;
    };

    const pillarPositions: { x: number; z: number }[] = [];
    const pillarRings = [
      { count: 24, radius: 18 },
      { count: 32, radius: 32 },
      { count: 36, radius: 50 },
      { count: 32, radius: 65 },
      { count: 40, radius: 85 },
      { count: 48, radius: 110 },
    ];

    pillarRings.forEach((ring) => {
      for (let i = 0; i < ring.count; i++) {
        const angle = (i / ring.count) * Math.PI * 2;
        pillarPositions.push({
          x: Math.cos(angle) * ring.radius,
          z: Math.sin(angle) * ring.radius,
        });
      }
    });

    const hitPillar = (x: number, z: number) => {
      for (const p of pillarPositions) {
        if ((x - p.x) ** 2 + (z - p.z) ** 2 < 3.2) return true;
      }
      return false;
    };

    const streetRadii = [9, 25, 41, 57.5, 75, 97.5, 120];

    streetRadii.forEach((r) => {
      const spacing = 0.4 + Math.random() * 0.4;
      const laneWidth = 0.2 + Math.random() * 0.3;
      const circum = 2 * Math.PI * r;
      const steps = Math.floor(circum / spacing);

      for (let i = 0; i < steps; i++) {
        if (Math.random() < 0.05) continue;

        const angle = (i / steps) * Math.PI * 2;
        const cx = Math.cos(angle) * r;
        const cz = Math.sin(angle) * r;
        const px = Math.cos(angle) * laneWidth;
        const pz = Math.sin(angle) * laneWidth;

        if (!hitPillar(cx + px, cz + pz)) addLight(cx + px, cz + pz);
        if (!hitPillar(cx - px, cz - pz)) addLight(cx - px, cz - pz);
      }
    });

    pillarRings.forEach((ring, idx) => {
      const rStart = streetRadii[idx];
      const rEnd = streetRadii[idx + 1];
      const length = rEnd - rStart;

      for (let i = 0; i < ring.count; i++) {
        if (Math.random() > 0.4) continue;

        const spacing = 0.4 + Math.random() * 0.4;
        const laneWidth = 0.15 + Math.random() * 0.3;
        const steps = Math.floor(length / spacing);
        const angleGap = ((i + 0.5) / ring.count) * Math.PI * 2;

        for (let j = 0; j < steps; j++) {
          const currentR = rStart + (j / steps) * length;
          if (currentR < rStart + 1.0 || currentR > rEnd - 1.0) continue;

          const angleOffset = laneWidth / currentR;
          const lx = Math.cos(angleGap - angleOffset) * currentR;
          const lz = Math.sin(angleGap - angleOffset) * currentR;
          const rx = Math.cos(angleGap + angleOffset) * currentR;
          const rz = Math.sin(angleGap + angleOffset) * currentR;

          if (!hitPillar(lx, lz)) addLight(lx, lz);
          if (!hitPillar(rx, rz)) addLight(rx, rz);
        }
      }
    });

    interface Tendril {
      x: number;
      z: number;
      angle: number;
      curl: number;
      spacing: number;
      laneWidth: number;
      life: number;
    }

    let tendrils: Tendril[] = [];

    for (let i = 0; i < 45; i++) {
      const startX = (Math.random() - 0.5) * 6;
      const startZ = (Math.random() - 0.5) * 6;
      const startAngle = Math.random() * Math.PI * 2;

      tendrils.push({
        x: startX,
        z: startZ,
        angle: startAngle,
        curl: (Math.random() - 0.5) * 0.1,
        spacing: 0.35 + Math.random() * 0.4,
        laneWidth: 0.15 + Math.random() * 0.3,
        life: 150 + Math.random() * 200,
      });
    }

    while (tendrils.length > 0 && spawnedCount < targetCount) {
      const nextGen: Tendril[] = [];

      for (const t of tendrils) {
        if (t.life <= 0 || t.x * t.x + t.z * t.z > 14400) continue;

        t.curl += (Math.random() - 0.5) * 0.03;
        t.curl = Math.max(-0.08, Math.min(0.08, t.curl));
        t.angle += t.curl;

        t.x += Math.cos(t.angle) * t.spacing;
        t.z += Math.sin(t.angle) * t.spacing;

        const px = Math.cos(t.angle + Math.PI / 2) * t.laneWidth;
        const pz = Math.sin(t.angle + Math.PI / 2) * t.laneWidth;

        let hitObstacle = false;
        if (!hitPillar(t.x + px, t.z + pz)) addLight(t.x + px, t.z + pz);
        else hitObstacle = true;

        if (!hitPillar(t.x - px, t.z - pz)) addLight(t.x - px, t.z - pz);
        else hitObstacle = true;

        if (hitObstacle) {
          t.curl *= -1.5;
          t.angle += t.curl * 2;
        }

        t.life--;

        if (t.life > 30 && Math.random() < 0.025 && nextGen.length + tendrils.length < 150) {
          nextGen.push({
            x: t.x,
            z: t.z,
            angle: t.angle + (Math.random() > 0.5 ? 0.6 : -0.6),
            curl: -t.curl + (Math.random() - 0.5) * 0.05,
            spacing: t.spacing,
            laneWidth: t.laneWidth * (0.7 + Math.random() * 0.5),
            life: t.life * 0.8,
          });
        }
        nextGen.push(t);
      }
      tendrils = nextGen;
    }

    let tCenter = 0;
    while (spawnedCount < targetCount) {
      const cx = Math.sin(tCenter * 0.1) * (tCenter * 0.05) * Math.cos(tCenter * 0.02);
      const cz = Math.cos(tCenter * 0.1) * (tCenter * 0.05) * Math.sin(tCenter * 0.03);
      addLight(cx, cz);
      tCenter += 0.4;
    }

    // --- 5. BUFFER ASSEMBLY ---
    // The geometry now strictly matches this.streetLightCount
    this.streetLightBaseX = new Float32Array(tempBaseX);
    this.streetLightBaseZ = new Float32Array(tempBaseZ);
    const positions = new Float32Array(tempPositions);
    const seeds = new Float32Array(tempSeeds);

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('twinkleSeed', new THREE.BufferAttribute(seeds, 1));

    this.streetLightMaterial = new THREE.ShaderMaterial({
      uniforms: {
        mouseNDC: { value: new THREE.Vector2(2, 2) },
        aspect: { value: 1.0 },
        baseSize: { value: 6.0 },
        time: { value: 0.0 },
        color: { value: new THREE.Color(0xffb066) },
        globalOpacity: { value: 0.0 },
        fogMinDist: this.fogUniforms.fogMinDist,
        fogMaxDist: this.fogUniforms.fogMaxDist,
      },
      vertexShader: PS1_LIGHT_POINTS_VERTEX,
      fragmentShader: PS1_LIGHT_POINTS_FRAGMENT,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    this.streetLightPoints = new THREE.Points(geometry, this.streetLightMaterial);
    this.streetLightPoints.frustumCulled = false;
    this.streetLightPoints.renderOrder = 99;
    this.group.add(this.streetLightPoints);
  }

  public setLayer(index: number): void {
    this.currentLayer = index;

    this.coreMaterial.wireframe = index < 2;

    this.targetReveal = index >= 1 ? 1.0 : 0.0;
    this.targetSolid = index >= 2 ? 1.0 : 0.0;
    this.targetNormalMap = index === 2 ? 1.0 : 0.0;
    this.targetLight = index >= 3 ? 1.0 : 0.0;
    this.targetSky = index >= 3 ? 1.0 : 0.0;
    this.targetDisplacement = index >= 3 ? 1.0 : 0.0;
    this.targetFog = index >= 3 ? 1.0 : 0.0;
    this.targetParticleLights = index >= 3 ? 1.0 : 0.0;
    this.targetResolution = index === 3 ? 300.0 : 90.0;

    for (const p of this.pillarMeshes) {
      const ring = p.userData['ringIndex'];
      const target = ring === 0 ? (index >= 2 ? 1 : 0) : index >= 3 ? 1 : 0;
      p.userData['targetProgress'] = target;
    }
  }

  public update(coreSpeed: number, time: number): void {
    this.core.rotation.y += coreSpeed;
    this.core.rotation.x += coreSpeed * 0.4;

    const resSpeed = this.targetResolution > this.currentResolution ? 0.03 : 0.08;
    this.currentResolution += (this.targetResolution - this.currentResolution) * resSpeed;

    this.coreMaterial.uniforms['resolution'].value = this.currentResolution;
    this.solidMaterial.uniforms['resolution'].value = this.currentResolution;
    this.skyMaterial.uniforms['resolution'].value = this.currentResolution;
    for (const p of this.pillarMeshes) {
      (p.material as THREE.ShaderMaterial).uniforms['resolution'].value = this.currentResolution;
    }

    const revealSpeed = this.targetReveal < this.revealProgress ? 0.15 : 0.06;
    this.revealProgress += (this.targetReveal - this.revealProgress) * revealSpeed;
    this.solidMaterial.uniforms['revealProgress'].value = this.revealProgress;

    const solidSpeed = this.targetSolid < this.solidState ? 0.15 : 0.06;
    this.solidState += (this.targetSolid - this.solidState) * solidSpeed;
    this.solidMaterial.uniforms['isSolid'].value = this.solidState;

    const normalSpeed = this.targetNormalMap < this.normalMapState ? 0.08 : 0.02;
    this.normalMapState += (this.targetNormalMap - this.normalMapState) * normalSpeed;
    this.coreMaterial.uniforms['useNormalMap'].value = this.normalMapState;

    const skySpeed = this.targetSky < this.skyOpacity ? 0.08 : 0.02;
    this.skyOpacity += (this.targetSky - this.skyOpacity) * skySpeed;
    this.skyMaterial.uniforms['opacity'].value = this.skyOpacity;

    const lightSpeed = this.targetLight < this.lightState ? 0.08 : 0.02;
    this.lightState += (this.targetLight - this.lightState) * lightSpeed;
    this.coreMaterial.uniforms['useLighting'].value = this.lightState;
    this.solidMaterial.uniforms['useLighting'].value = this.lightState;
    for (const p of this.pillarMeshes) {
      (p.material as THREE.ShaderMaterial).uniforms['useLighting'].value = this.lightState;
    }
    this.skyMaterial.uniforms['useLighting'].value = this.lightState;

    const fogSpeed = this.targetFog < this.fogEnabled ? 0.08 : 0.02;
    this.fogEnabled += (this.targetFog - this.fogEnabled) * fogSpeed;
    const minDist = this.fogEnabled > 0.5 ? 6.0 : 1000.0;
    const maxDist = this.fogEnabled > 0.5 ? 28.0 : 2000.0;
    this.fogUniforms.fogMinDist.value = minDist;
    this.fogUniforms.fogMaxDist.value = maxDist;

    const dispSpeed = this.targetDisplacement < this.displacementAmount ? 0.08 : 0.02;
    this.displacementAmount += (this.targetDisplacement - this.displacementAmount) * dispSpeed;
    this.solidMaterial.uniforms['displacementAmount'].value = this.displacementAmount;

    for (const p of this.pillarMeshes) {
      const target = p.userData['targetProgress'];
      const current = p.userData['progress'];
      const speed = target < current ? 0.15 : 0.06 * p.userData['growthSpeed'];
      let newProgress = current + (target - current) * speed;
      newProgress = Math.min(Math.max(newProgress, 0), 1);
      p.userData['progress'] = newProgress;

      const yOffset = -35.0 + newProgress * 30.0;
      p.position.y = yOffset;

      const opacityTarget = target;
      const opacityCurrent = p.userData['opacityProgress'];
      const opacitySpeed = opacityTarget > opacityCurrent ? 0.25 : 0.15;
      let newOpacity = opacityCurrent + (opacityTarget - opacityCurrent) * opacitySpeed;
      newOpacity = Math.min(Math.max(newOpacity, 0), 1);
      p.userData['opacityProgress'] = newOpacity;

      const mat = p.material as THREE.ShaderMaterial;
      mat.uniforms['opacity'].value = newOpacity;

      const isOpaque = newOpacity > 0.99;
      if (isOpaque && mat.transparent) {
        mat.transparent = false;
        mat.needsUpdate = true;
      } else if (!isOpaque && !mat.transparent) {
        mat.transparent = true;
        mat.needsUpdate = true;
      }
    }

    this.skyMaterial.uniforms['time'].value = time;
    this.coreMaterial.uniforms['time'].value = time;

    const particleSpeed = this.targetParticleLights < this.particleLightsState ? 0.08 : 0.03;
    this.particleLightsState +=
      (this.targetParticleLights - this.particleLightsState) * particleSpeed;
    this.streetLightMaterial.uniforms['globalOpacity'].value = this.particleLightsState;
    this.streetLightMaterial.uniforms['time'].value = time;

    const streetPositions = this.streetLightPoints.geometry.attributes[
      'position'
    ] as THREE.BufferAttribute;
    for (let i = 0; i < this.streetLightCount; i++) {
      const x = this.streetLightBaseX[i];
      const z = this.streetLightBaseZ[i];
      const h = this.terrainHeightAt(x, z) * this.displacementAmount;
      streetPositions.setXYZ(i, x, -5.0 + h + 0.65, z);
    }
    streetPositions.needsUpdate = true;
  }

  public shiftColors(hexColor: number): void {
    this.coreMaterial.uniforms['color'].value.setHex(hexColor);
    this.solidMaterial.uniforms['color'].value.setHex(hexColor).multiplyScalar(0.4);
    this.skyMaterial.uniforms['color'].value.setHex(hexColor);
    for (const p of this.pillarMeshes) {
      const mat = p.material as THREE.ShaderMaterial;
      mat.uniforms['color'].value.setHex(hexColor).multiplyScalar(0.4);
    }
  }

  public swapShape(index: number): void {
    this.core.geometry = this.shapes[index % this.shapes.length];
  }
}
