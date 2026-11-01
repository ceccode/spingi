/** Three.js scene built from scene.yaml (and terrain.json outdoors). MuJoCo coordinates (Z up) inside a rotated group. */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/examples/jsm/libs/meshopt_decoder.module.js";
import { MAX_JOINTS, type BaseLift, type Pose2D, type Scene } from "./episode";
import { heightAt, terrainGeometry, type Terrain } from "./terrain";

const OBSTACLE_COLOR = 0x8a8f9c;
const HEX = /^#[0-9a-fA-F]{6}$/;

/** Fine grain multiplied over the surface colours: stones, asphalt aggregate, blades of grass at a glance. 1 m per tile. */
function grainTexture(): THREE.DataTexture {
  const size = 256;
  const data = new Uint8Array(size * size * 4);
  let seed = 12345;
  for (let i = 0; i < size * size; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const v = 170 + ((seed >>> 24) % 86);
    data.set([v, v, v, 255], i * 4);
  }
  const tex = new THREE.DataTexture(data, size, size);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.magFilter = THREE.NearestFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter; // no shimmering in the distance
  tex.generateMipmaps = true;
  tex.anisotropy = 4;
  tex.needsUpdate = true;
  return tex;
}

interface JointNode { node: THREE.Object3D; axis: THREE.Vector3; rest: THREE.Quaternion; stand: number }

export class World {
  readonly root = new THREE.Group(); // Z-up → Y-up
  readonly robot = new THREE.Group();
  private readonly labels: THREE.Sprite[] = [];
  private readonly objects = new Map<string, { mesh: THREE.Mesh; label: THREE.Sprite; halfHeight: number }>();
  private joints = new Map<string, JointNode>();
  private base: THREE.Object3D | null = null; // the robot's root body: carries height and tilt
  private baseZ = 0;
  private terrain: Terrain | null = null;

  constructor(readonly three: THREE.Scene) {
    this.root.rotation.x = -Math.PI / 2;
    three.add(this.root);
    this.root.add(this.robot);
  }

  build(scene: Scene, terrain: Terrain | null = null): void {
    for (const child of [...this.root.children]) if (child !== this.robot) this.root.remove(child);
    this.labels.length = 0;
    this.objects.clear();
    this.terrain = terrain;
    const ground = (x: number, y: number): number => (terrain ? heightAt(terrain, x, y) : 0);

    if (terrain) {
      const g = terrainGeometry(terrain);
      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(g.positions, 3));
      geom.setAttribute("color", new THREE.BufferAttribute(g.colors, 3));
      geom.setAttribute("uv", new THREE.BufferAttribute(g.uvs, 2));
      geom.setIndex(new THREE.BufferAttribute(g.indices, 1));
      geom.computeVertexNormals();
      const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({ vertexColors: true, map: grainTexture(), roughness: 0.95 }));
      mesh.receiveShadow = true;
      this.root.add(mesh);
      let low = Infinity;
      for (const h of terrain.heights) low = Math.min(low, h);
      const field = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshStandardMaterial({ color: 0x5f6b48, roughness: 1 }));
      field.position.z = low - 0.05; // around the terrain, like the simulator's backstop
      field.receiveShadow = true;
      this.root.add(field);
    } else {
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), new THREE.MeshStandardMaterial({ color: 0x3a3d47, roughness: 1 }));
      floor.receiveShadow = true;
      this.root.add(floor);
      const grid = new THREE.GridHelper(60, 60, 0x50545f, 0x2b2f3a);
      grid.rotation.x = Math.PI / 2;
      grid.position.z = 0.002;
      this.root.add(grid);
    }

    for (const [name, loc] of Object.entries(scene.locations ?? {})) {
      const r = loc.tolerance_m ? Math.max(0.25, loc.tolerance_m) : 0.25;
      const z = ground(loc.pose.x, loc.pose.y);
      const disc = new THREE.Mesh(new THREE.CircleGeometry(r, 48), new THREE.MeshBasicMaterial({ color: 0x5b7cfa, transparent: true, opacity: 0.45 }));
      disc.position.set(loc.pose.x, loc.pose.y, z + 0.01);
      this.root.add(disc);
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(Math.cos(loc.pose.yaw), Math.sin(loc.pose.yaw), 0), new THREE.Vector3(loc.pose.x, loc.pose.y, z + 0.02), 0.35, 0x8fa4ff, 0.12, 0.08);
      this.root.add(arrow);
      this.root.add(this.label(name, loc.pose.x, loc.pose.y, z + 0.5));
    }

    (scene.obstacles ?? []).forEach((obs, i) => {
      const w = obs.w ?? 0.5, d = obs.d ?? 0.5, h = obs.h ?? 1;
      const color = typeof obs.color === "string" && HEX.test(obs.color) ? obs.color : OBSTACLE_COLOR;
      const box = new THREE.Mesh(new THREE.BoxGeometry(w, d, h), new THREE.MeshStandardMaterial({ color, roughness: 0.85 }));
      box.position.set(obs.x, obs.y, (terrain?.obstaclesZ[i] ?? 0) + h / 2);
      box.castShadow = true;
      box.receiveShadow = true;
      this.root.add(box);
    });

    for (const [id, obj] of Object.entries(scene.objects ?? {})) {
      const size = obj.size ?? [0.15, 0.1, 0.1];
      const box = new THREE.Mesh(new THREE.BoxGeometry(size[0]!, size[1]!, size[2]!), new THREE.MeshStandardMaterial({ color: 0xd9473f, roughness: 0.6 }));
      const p = obj.pose ?? { x: 0, y: 0, z: size[2]! / 2 };
      box.position.set(p.x, p.y, p.z);
      box.castShadow = true;
      box.name = `obj_${id}`;
      this.root.add(box);
      const label = this.label(id, p.x, p.y, p.z + 0.25);
      this.root.add(label);
      this.objects.set(id, { mesh: box, label, halfHeight: size[2]! / 2 });
    }
  }

  /** Loads the robot's GLB and its joint table (which node each joint turns). Without the table the robot is shown
   *  rigid, in the pose the GLB has. */
  async loadRobot(url: string, jointsUrl?: string): Promise<void> {
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
    this.joints = new Map();
    this.base = null;
    if (!jointsUrl) return;
    const res = await fetch(jointsUrl);
    if (!res.ok) return;
    const table = (await res.json()) as { root?: unknown; base_z?: unknown; joints?: unknown };
    if (typeof table.root === "string") this.base = gltf.scene.getObjectByName(table.root) ?? null;
    this.baseZ = typeof table.base_z === "number" && Number.isFinite(table.base_z) ? table.base_z : 0;
    for (const j of Array.isArray(table.joints) ? table.joints.slice(0, MAX_JOINTS) : []) {
      const { name, body, axis, stand } = j as { name?: unknown; body?: unknown; axis?: unknown; stand?: unknown };
      if (typeof name !== "string" || typeof body !== "string" || typeof stand !== "number") continue;
      if (!Array.isArray(axis) || axis.length !== 3 || !axis.every((v) => typeof v === "number")) continue;
      const node = gltf.scene.getObjectByName(body);
      if (!node) continue;
      this.joints.set(name, { node, axis: new THREE.Vector3(...(axis as [number, number, number])).normalize(), rest: node.quaternion.clone(), stand });
    }
    this.setJoints(null);
  }

  /** Joint angles to show; a joint the episode does not mention stands as in the standing pose. */
  setJoints(values: Record<string, number> | null): void {
    const turn = new THREE.Quaternion();
    for (const [name, j] of this.joints) {
      const q = values?.[name] ?? j.stand;
      j.node.quaternion.copy(j.rest).multiply(turn.setFromAxisAngle(j.axis, q));
    }
  }

  /** Moves the objects whose position the episode reports (carried or placed); the others stay where the scene put them. */
  setObjectPositions(positions: Record<string, { x: number; y: number; z: number }>): void {
    for (const [id, p] of Object.entries(positions)) {
      const entry = this.objects.get(id);
      if (!entry) continue;
      entry.mesh.position.set(p.x, p.y, p.z);
      entry.label.position.set(p.x, p.y, p.z + 0.25);
    }
  }

  setRobotPose(pose: Pose2D & BaseLift): void {
    this.robot.position.set(pose.x, pose.y, 0);
    this.robot.rotation.set(0, 0, pose.yaw);
    if (this.base) {
      // The base's height and tilt when the episode has them; otherwise standing on the ground under it.
      const z = pose.z ?? (this.terrain ? heightAt(this.terrain, pose.x, pose.y) : 0) + this.baseZ;
      this.base.position.set(0, 0, z);
      this.base.rotation.set(pose.roll ?? 0, pose.pitch ?? 0, 0, "YXZ");
    } else if (pose.z !== undefined) {
      this.robot.position.z = pose.z - this.baseZ;
    }
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
