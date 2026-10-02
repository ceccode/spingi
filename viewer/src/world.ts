/** Three.js scene built from scene.yaml. MuJoCo coordinates (Z up) inside a rotated group. */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import type { Pose2D, Scene } from "./episode";

export class World {
  readonly root = new THREE.Group(); // Z-up → Y-up
  readonly robot = new THREE.Group();
  private readonly labels: THREE.Sprite[] = [];

  constructor(readonly three: THREE.Scene) {
    this.root.rotation.x = -Math.PI / 2;
    three.add(this.root);
    this.root.add(this.robot);
  }

  build(scene: Scene): void {
    for (const child of [...this.root.children]) if (child !== this.robot) this.root.remove(child);
    this.labels.length = 0;

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshStandardMaterial({ color: 0x3a3d47, roughness: 1 }));
    floor.receiveShadow = true;
    this.root.add(floor);
    const grid = new THREE.GridHelper(60, 60, 0x50545f, 0x2b2f3a);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = 0.002;
    this.root.add(grid);

    for (const [name, loc] of Object.entries(scene.locations ?? {})) {
      const r = loc.tolerance_m ? Math.max(0.25, loc.tolerance_m) : 0.25;
      const disc = new THREE.Mesh(new THREE.CircleGeometry(r, 48), new THREE.MeshBasicMaterial({ color: 0x5b7cfa, transparent: true, opacity: 0.45 }));
      disc.position.set(loc.pose.x, loc.pose.y, 0.004);
      this.root.add(disc);
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(Math.cos(loc.pose.yaw), Math.sin(loc.pose.yaw), 0), new THREE.Vector3(loc.pose.x, loc.pose.y, 0.01), 0.35, 0x8fa4ff, 0.12, 0.08);
      this.root.add(arrow);
      this.root.add(this.label(name, loc.pose.x, loc.pose.y, 0.5));
    }

    for (const obs of scene.obstacles ?? []) {
      const w = obs.w ?? 0.5, d = obs.d ?? 0.5, h = obs.h ?? 1;
      const box = new THREE.Mesh(new THREE.BoxGeometry(w, d, h), new THREE.MeshStandardMaterial({ color: 0x8a8f9c, roughness: 0.9 }));
      box.position.set(obs.x, obs.y, h / 2);
      box.castShadow = true;
      this.root.add(box);
    }

    for (const [id, obj] of Object.entries(scene.objects ?? {})) {
      const size = obj.size ?? [0.15, 0.1, 0.1];
      const box = new THREE.Mesh(new THREE.BoxGeometry(size[0]!, size[1]!, size[2]!), new THREE.MeshStandardMaterial({ color: 0xd9473f, roughness: 0.6 }));
      const p = obj.pose ?? { x: 0, y: 0, z: size[2]! / 2 };
      box.position.set(p.x, p.y, p.z);
      box.castShadow = true;
      box.name = `obj_${id}`;
      this.root.add(box);
      this.root.add(this.label(id, p.x, p.y, p.z + 0.25));
    }
  }

  async loadRobot(url: string): Promise<void> {
    const loader = new GLTFLoader();
    loader.setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.loadAsync(url);
    gltf.scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) {
        const m = o as THREE.Mesh;
        m.castShadow = true;
        const mat = m.material as THREE.MeshStandardMaterial;
        if (mat && "roughness" in mat) { mat.roughness = 0.55; mat.metalness = 0.25; }
      }
    });
    this.robot.clear();
    this.robot.add(gltf.scene);
  }

  setRobotPose(pose: Pose2D): void {
    this.robot.position.set(pose.x, pose.y, 0);
    this.robot.rotation.set(0, 0, pose.yaw);
  }

  /** Robot position in Three scene space (Y up), for the camera. */
  robotWorldPosition(target: THREE.Vector3): THREE.Vector3 {
    return this.robot.getWorldPosition(target);
  }

  faceCamera(camera: THREE.Camera): void {
    for (const s of this.labels) s.quaternion.copy(camera.quaternion);
  }

  private label(text: string, x: number, y: number, z: number): THREE.Sprite {
    const canvas = document.createElement("canvas");
    canvas.width = 256; canvas.height = 64;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "rgba(20,22,28,0.7)";
    ctx.fillRect(0, 0, 256, 64);
    ctx.font = "600 28px system-ui, sans-serif";
    ctx.fillStyle = "#e8eaf0";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(text, 128, 32);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthTest: false, sizeAttenuation: false }));
    sprite.scale.set(0.16, 0.04, 1); // constant on-screen size
    sprite.position.set(x, y, z);
    this.labels.push(sprite);
    return sprite;
  }
}
