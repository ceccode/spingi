import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { dispose, World } from "./world";

/** Records which of the GPU resources under `root` are freed from now on. */
function watchDisposal(root: THREE.Object3D): { freed: Set<unknown>; all: Set<unknown> } {
  const freed = new Set<unknown>();
  const all = new Set<unknown>();
  root.traverse((o) => {
    const { geometry, material } = o as Partial<THREE.Mesh>;
    for (const resource of [geometry, material].flat()) {
      if (!resource) continue;
      all.add(resource);
      resource.addEventListener("dispose", () => freed.add(resource));
    }
  });
  return { freed, all };
}

describe("GPU memory", () => {
  it("is freed for the geometry, the material and the textures of everything under a node", () => {
    const texture = new THREE.Texture();
    let textureFreed = false;
    texture.addEventListener("dispose", () => (textureFreed = true));
    const group = new THREE.Group();
    group.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ map: texture })));
    group.add(new THREE.Mesh(new THREE.PlaneGeometry(), [new THREE.MeshBasicMaterial(), new THREE.MeshBasicMaterial()]));
    const { freed, all } = watchDisposal(group);
    dispose(group);
    expect(all.size).toBe(5);
    expect(freed).toEqual(all);
    expect(textureFreed).toBe(true);
  });
  it("is freed for the previous episode's scene when a new one is built, and the robot's belief marker is kept", () => {
    const world = new World(new THREE.Scene());
    world.build({ obstacles: [{ x: 1, y: 1 }, { x: 2, y: 2, color: "#ff0000" }] });
    const boxes = (): THREE.Mesh[] => {
      const found: THREE.Mesh[] = [];
      world.root.traverse((o) => { if ((o as THREE.Mesh).geometry instanceof THREE.BoxGeometry) found.push(o as THREE.Mesh); });
      return found;
    };
    const first = boxes();
    expect(first).toHaveLength(2);
    const { freed, all } = watchDisposal(world.root);
    world.build({ obstacles: [{ x: 3, y: 3 }] });
    const second = boxes();
    expect(second).toHaveLength(1);
    for (const box of first) {
      expect(freed.has(box.geometry)).toBe(true);
      expect(freed.has(box.material)).toBe(true);
    }
    const kept = [...all].filter((r) => !freed.has(r));
    expect(kept.some((r) => r instanceof THREE.RingGeometry)).toBe(true); // the belief marker lives across episodes
  });
});

describe("a scene from a stranger", () => {
  it("is built without what is malformed in it, never half-way", () => {
    const world = new World(new THREE.Scene());
    const odd = { locations: { a: {}, b: "here" }, obstacles: [null, { x: "far", y: 0 }, { x: 1, y: 1 }], objects: { c: { pose: 3 } }, landmarks: [{}] };
    expect(() => world.build(odd as never)).not.toThrow();
    let boxes = 0;
    world.root.traverse((o) => { if ((o as THREE.Mesh).geometry instanceof THREE.BoxGeometry) boxes++; });
    expect(boxes).toBe(1);
  });
});
