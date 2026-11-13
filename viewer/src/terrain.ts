/** The ground of an outdoor episode (`terrain.json`, written by the runtime): heights on a grid and the colour of
 *  each surface. Episodes come from strangers too, so everything is checked before it reaches the GPU. No 3D here. */

export interface TerrainSurface {
  name: string;
  rgb: [number, number, number];
  speck: [number, number, number];
}
export interface Terrain {
  x0: number;
  y0: number;
  cellM: number;
  rows: number;
  cols: number;
  heights: Float32Array; // metres, row-major, row = y index
  ids: Uint8Array; // index into `surfaces`, per sample
  surfaces: TerrainSurface[];
  obstaclesZ: number[]; // where each scene obstacle stands
}

export const MAX_TERRAIN_SAMPLES = 1_000_000;
const MAX_SURFACES = 32;

export function parseTerrain(raw: unknown): Terrain {
  const t = raw as Record<string, unknown>;
  if (typeof t !== "object" || t === null || t.format !== 1) throw new Error("terrain.json: unknown format");
  const num = (v: unknown, what: string): number => {
    if (typeof v !== "number" || !Number.isFinite(v)) throw new Error(`terrain.json: ${what} is not a number`);
    return v;
  };
  const rows = num(t.rows, "rows"),
    cols = num(t.cols, "cols"),
    cellM = num(t.cell_m, "cell_m");
  if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows < 2 || cols < 2 || rows * cols > MAX_TERRAIN_SAMPLES) {
    throw new Error(`terrain.json: grid ${rows}x${cols} refused`);
  }
  if (cellM <= 0 || cellM > 10) throw new Error("terrain.json: cell_m out of range");
  const mm = decodeBase64(t.heights_mm, rows * cols * 2, "heights_mm");
  const ids = decodeBase64(t.surface_ids, rows * cols, "surface_ids");
  const view = new DataView(mm.buffer, mm.byteOffset, mm.byteLength);
  const heights = new Float32Array(rows * cols);
  for (let i = 0; i < heights.length; i++) heights[i] = view.getInt16(i * 2, true) / 1000;
  if (!Array.isArray(t.surfaces) || t.surfaces.length === 0 || t.surfaces.length > MAX_SURFACES) {
    throw new Error("terrain.json: surfaces missing or too many");
  }
  const surfaces = t.surfaces.map((s, i): TerrainSurface => {
    const o = s as Record<string, unknown>;
    return {
      name: typeof o?.name === "string" ? o.name.slice(0, 32) : `surface ${i}`,
      rgb: colour(o?.rgb),
      speck: colour(o?.speck),
    };
  });
  for (const id of ids) if (id >= surfaces.length) throw new Error("terrain.json: surface id out of range");
  const obstaclesZ = Array.isArray(t.obstacles_z)
    ? t.obstacles_z.slice(0, 10_000).map((v) => num(v, "obstacles_z"))
    : [];
  return { x0: num(t.x0, "x0"), y0: num(t.y0, "y0"), cellM, rows, cols, heights, ids, surfaces, obstaclesZ };
}

function colour(v: unknown): [number, number, number] {
  if (!Array.isArray(v) || v.length !== 3 || !v.every((c) => typeof c === "number" && c >= 0 && c <= 1)) {
    throw new Error("terrain.json: a surface colour is not three numbers between 0 and 1");
  }
  return [v[0], v[1], v[2]];
}

function decodeBase64(v: unknown, bytes: number, what: string): Uint8Array {
  if (typeof v !== "string" || v.length !== 4 * Math.ceil(bytes / 3))
    throw new Error(`terrain.json: ${what} has the wrong size`);
  const bin = atob(v);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  if (out.length !== bytes) throw new Error(`terrain.json: ${what} has the wrong size`);
  return out;
}

/** Ground height at a point, bilinear; outside the grid, the nearest edge. */
export function heightAt(t: Terrain, x: number, y: number): number {
  const fx = Math.min(Math.max((x - t.x0) / t.cellM, 0), t.cols - 1);
  const fy = Math.min(Math.max((y - t.y0) / t.cellM, 0), t.rows - 1);
  const c = Math.min(Math.floor(fx), t.cols - 2),
    r = Math.min(Math.floor(fy), t.rows - 2);
  const u = fx - c,
    v = fy - r;
  const h = (rr: number, cc: number): number => t.heights[rr * t.cols + cc]!;
  const top = h(r, c) * (1 - u) + h(r, c + 1) * u;
  const bottom = h(r + 1, c) * (1 - u) + h(r + 1, c + 1) * u;
  return top * (1 - v) + bottom * v;
}

/** A vertex per sample in MuJoCo coordinates (Z up), coloured by surface with a little grain, and two triangles per
 *  cell. Plain arrays: the caller turns them into a mesh. */
export function terrainGeometry(t: Terrain): {
  positions: Float32Array;
  colors: Float32Array;
  uvs: Float32Array;
  indices: Uint32Array;
} {
  const n = t.rows * t.cols;
  const positions = new Float32Array(n * 3);
  const colors = new Float32Array(n * 3);
  const uvs = new Float32Array(n * 2); // in metres: a repeating grain texture keeps its size on every surface
  for (let r = 0; r < t.rows; r++) {
    for (let c = 0; c < t.cols; c++) {
      const i = r * t.cols + c;
      positions[i * 3] = t.x0 + c * t.cellM;
      positions[i * 3 + 1] = t.y0 + r * t.cellM;
      positions[i * 3 + 2] = t.heights[i]!;
      uvs[i * 2] = positions[i * 3]!;
      uvs[i * 2 + 1] = positions[i * 3 + 1]!;
      const s = t.surfaces[t.ids[i]!]!;
      const mix = 0.35 * hash(r, c); // deterministic, so the ground looks the same at every replay
      for (let k = 0; k < 3; k++) colors[i * 3 + k] = s.rgb[k]! * (1 - mix) + s.speck[k]! * mix;
    }
  }
  const indices = new Uint32Array((t.rows - 1) * (t.cols - 1) * 6);
  let j = 0;
  for (let r = 0; r < t.rows - 1; r++) {
    for (let c = 0; c < t.cols - 1; c++) {
      const a = r * t.cols + c,
        b = a + 1,
        d = a + t.cols,
        e = d + 1;
      indices.set([a, b, e, a, e, d], j);
      j += 6;
    }
  }
  return { positions, colors, uvs, indices };
}

function hash(r: number, c: number): number {
  let h = (r * 73856093) ^ (c * 19349663);
  h = Math.imul(h ^ (h >>> 13), 0x5bd1e995);
  return ((h ^ (h >>> 15)) >>> 0) / 4294967296;
}
