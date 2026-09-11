import { Injectable, ElementRef, NgZone } from '@angular/core';
import * as THREE from 'three';
import { LandscapeManager } from './landscape';

interface CameraWaypoint {
  position: THREE.Vector3;
  lookAt: THREE.Vector3;
}

@Injectable({
  providedIn: 'root',
})
export class ThreeService {
  private renderer!: THREE.WebGLRenderer;
  private scene!: THREE.Scene;
  private camera!: THREE.PerspectiveCamera;
  private landscape!: LandscapeManager;

  private cubeRenderTarget!: THREE.WebGLCubeRenderTarget;
  private cubeCamera!: THREE.CubeCamera;
  private activeShapeIndex = 0;
  private cubeUpdateCounter = 0;
  private readonly CUBE_UPDATE_INTERVAL = 3;
  private tempWorldPos = new THREE.Vector3();

  private startTime = 0;

  private frameId: number | null = null;
  private mouseX = 0;
  private mouseY = 0;

  private coreSpeed = 0.002;
  private spinMultiplier = 1.0;

  private targetScale = 1.0;
  private currentScale = 1.0;
  private pendingShape = -1;

  private initialized = false;
  private pendingTransition: { shapeIndex: number; themeHex: number } | null = null;

  private readonly CAMERA_POSITION_OFFSET_X = -10.0;
  private readonly CAMERA_POSITION_OFFSET_Y = 0.0;
  private readonly CAMERA_POSITION_OFFSET_Z = 8.0;

  private readonly CAMERA_LOOK_OFFSET_X = -6.0;
  private readonly CAMERA_LOOK_OFFSET_Y = 0.0;
  private readonly CAMERA_LOOK_OFFSET_Z = 0.0;

  private readonly RIDE_SAFE_ALTITUDE = 6.0;
  private readonly RIDE_SPEED = 0.045;

  private cameraBasePos = new THREE.Vector3(
    this.CAMERA_POSITION_OFFSET_X,
    this.CAMERA_POSITION_OFFSET_Y,
    this.CAMERA_POSITION_OFFSET_Z,
  );
  private cameraBaseLook = new THREE.Vector3(
    this.CAMERA_LOOK_OFFSET_X,
    this.CAMERA_LOOK_OFFSET_Y,
    this.CAMERA_LOOK_OFFSET_Z,
  );

  private rideTargetPos = this.cameraBasePos.clone();
  private rideTargetLook = this.cameraBaseLook.clone();
  private ridePhase: 'idle' | 'ascend' | 'glide' | 'descend' = 'idle';
  private activeWaypointId = 'default';

  private cameraWaypoints: Record<string, CameraWaypoint> = {
    default: {
      position: new THREE.Vector3(
        this.CAMERA_POSITION_OFFSET_X,
        this.CAMERA_POSITION_OFFSET_Y,
        this.CAMERA_POSITION_OFFSET_Z,
      ),
      lookAt: new THREE.Vector3(
        this.CAMERA_LOOK_OFFSET_X,
        this.CAMERA_LOOK_OFFSET_Y,
        this.CAMERA_LOOK_OFFSET_Z,
      ),
    },
    do_containers: {
      position: new THREE.Vector3(-9, 6, 12),
      lookAt: new THREE.Vector3(-3, 1, 2),
    },
    do_pipelines: {
      position: new THREE.Vector3(5, -2.5, 11),
      lookAt: new THREE.Vector3(0, 4.5, 0),
    },
    do_observability: {
      position: new THREE.Vector3(-11, 6, -7),
      lookAt: new THREE.Vector3(-3, 2, -13),
    },
  };

  constructor(private ngZone: NgZone) {}

  public initialize(canvas: ElementRef<HTMLCanvasElement>): void {
    const width = window.innerWidth;
    const height = window.innerHeight;

    this.startTime = performance.now();
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x03040a);

    this.camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 800);
    this.camera.position.copy(this.cameraBasePos);
    this.camera.lookAt(this.cameraBaseLook);

    this.renderer = new THREE.WebGLRenderer({
      canvas: canvas.nativeElement,
      antialias: false,
      powerPreference: 'high-performance',
    });
    this.renderer.setSize(width, height);
    this.renderer.setPixelRatio(0.65);

    this.landscape = new LandscapeManager();
    this.landscape.initialize();
    this.landscape.setFog(8, 45, 0x8899aa);
    this.landscape.setAspect(this.camera.aspect);
    this.scene.add(this.landscape.getGroup());

    this.cubeRenderTarget = new THREE.WebGLCubeRenderTarget(128, {
      generateMipmaps: true,
      minFilter: THREE.LinearMipmapLinearFilter,
    });
    this.cubeRenderTarget.texture.colorSpace = THREE.SRGBColorSpace;
    this.cubeCamera = new THREE.CubeCamera(0.1, 200, this.cubeRenderTarget);
    this.landscape.setEnvMap(this.cubeRenderTarget.texture);

    window.addEventListener('resize', this.onWindowResize.bind(this));

    document.addEventListener('mousemove', (event) => {
      this.mouseX = (event.clientX / window.innerWidth) * 2 - 1;
      this.mouseY = -(event.clientY / window.innerHeight) * 2 + 1;
      this.landscape.setMouseNDC(this.mouseX, this.mouseY);
    });

    this.spinMultiplier = 2.0;
    this.initialized = true;

    if (this.pendingTransition) {
      this.triggerTransition(this.pendingTransition.shapeIndex, this.pendingTransition.themeHex);
      this.pendingTransition = null;
    }

    this.ngZone.runOutsideAngular(() => {
      this.animate();
    });
  }

  public triggerTransition(shapeIndex: number, themeHex: number): void {
    if (!this.initialized) {
      this.pendingTransition = { shapeIndex, themeHex };
      return;
    }

    this.activeShapeIndex = shapeIndex;
    this.spinMultiplier = 40.0;
    this.targetScale = 0.01;
    this.pendingShape = shapeIndex;
    this.landscape.shiftColors(themeHex);
    this.landscape.setLayer(shapeIndex);
  }

  public setCameraWaypoint(id: string): void {
    const waypoint = this.cameraWaypoints[id] ?? this.cameraWaypoints['default'];
    if (this.activeWaypointId === id && this.ridePhase === 'idle') return;

    this.activeWaypointId = id;
    this.rideTargetPos.copy(waypoint.position);
    this.rideTargetLook.copy(waypoint.lookAt);
    this.ridePhase = 'ascend';
  }

  private updateCameraRide(): void {
    if (this.ridePhase === 'idle') return;

    const speed = this.RIDE_SPEED;

    if (this.ridePhase === 'ascend') {
      const cruiseY = Math.max(this.rideTargetPos.y, this.RIDE_SAFE_ALTITUDE, this.cameraBasePos.y);
      this.cameraBasePos.y += (cruiseY - this.cameraBasePos.y) * speed;
      if (Math.abs(cruiseY - this.cameraBasePos.y) < 0.05) {
        this.cameraBasePos.y = cruiseY;
        this.ridePhase = 'glide';
      }
    } else if (this.ridePhase === 'glide') {
      const cruiseY = Math.max(this.rideTargetPos.y, this.RIDE_SAFE_ALTITUDE);
      this.cameraBasePos.x += (this.rideTargetPos.x - this.cameraBasePos.x) * speed;
      this.cameraBasePos.z += (this.rideTargetPos.z - this.cameraBasePos.z) * speed;
      this.cameraBasePos.y += (cruiseY - this.cameraBasePos.y) * speed;

      const dx = this.rideTargetPos.x - this.cameraBasePos.x;
      const dz = this.rideTargetPos.z - this.cameraBasePos.z;
      if (Math.sqrt(dx * dx + dz * dz) < 0.15) {
        this.cameraBasePos.x = this.rideTargetPos.x;
        this.cameraBasePos.z = this.rideTargetPos.z;
        this.ridePhase = 'descend';
      }
    } else if (this.ridePhase === 'descend') {
      this.cameraBasePos.y += (this.rideTargetPos.y - this.cameraBasePos.y) * speed;
      if (Math.abs(this.rideTargetPos.y - this.cameraBasePos.y) < 0.05) {
        this.cameraBasePos.y = this.rideTargetPos.y;
        this.ridePhase = 'idle';
      }
    }

    this.cameraBaseLook.x += (this.rideTargetLook.x - this.cameraBaseLook.x) * speed;
    this.cameraBaseLook.y += (this.rideTargetLook.y - this.cameraBaseLook.y) * speed;
    this.cameraBaseLook.z += (this.rideTargetLook.z - this.cameraBaseLook.z) * speed;
  }

  private updateCamera(): void {
    const targetCamX = this.cameraBasePos.x + this.mouseX * 1.5;
    const targetCamY = this.cameraBasePos.y + this.mouseY * 1.0;
    const targetCamZ = this.cameraBasePos.z;

    this.camera.position.x += (targetCamX - this.camera.position.x) * 0.05;
    this.camera.position.y += (targetCamY - this.camera.position.y) * 0.05;
    this.camera.position.z += (targetCamZ - this.camera.position.z) * 0.05;
    this.camera.lookAt(this.cameraBaseLook.x, this.cameraBaseLook.y, this.cameraBaseLook.z);
    this.camera.updateMatrixWorld(true);
  }

  private updateCubeReflection(): void {
    if (this.activeShapeIndex !== 3) return;

    this.cubeUpdateCounter++;
    if (this.cubeUpdateCounter < this.CUBE_UPDATE_INTERVAL) return;
    this.cubeUpdateCounter = 0;

    const core = this.landscape.getCore();
    core.getWorldPosition(this.tempWorldPos);
    this.cubeCamera.position.copy(this.tempWorldPos);
    core.visible = false;
    this.cubeCamera.update(this.renderer, this.scene);
    core.visible = true;
  }

  private animate(): void {
    this.frameId = requestAnimationFrame(() => this.animate());

    const time = (performance.now() - this.startTime) * 0.001;

    this.spinMultiplier += (1.0 - this.spinMultiplier) * 0.04;
    const currentSpin = this.coreSpeed * this.spinMultiplier;

    this.currentScale += (this.targetScale - this.currentScale) * 0.15;
    this.landscape.getCore().scale.setScalar(this.currentScale);

    if (this.targetScale === 0.01 && this.currentScale < 0.05 && this.pendingShape !== -1) {
      this.landscape.swapShape(this.pendingShape);
      this.targetScale = 1.0;
      this.pendingShape = -1;
    }

    this.updateCameraRide();
    this.updateCamera();

    this.landscape.update(currentSpin, time);

    this.updateCubeReflection();

    this.renderer.render(this.scene, this.camera);
  }

  private onWindowResize(): void {
    const width = window.innerWidth;
    const height = window.innerHeight;

    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.landscape.setAspect(this.camera.aspect);

    this.renderer.setSize(width, height);
  }
}
