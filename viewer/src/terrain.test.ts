import { describe, expect, it } from "vitest";
import { heightAt, MAX_TERRAIN_SAMPLES, parseTerrain, terrainGeometry } from "./terrain";

function encode(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

/** A 3x2 grid: heights in mm along x 0, 100, 200 (row 0) and 0, 100, 400 (row 1). */
function raw(over: Record<string, unknown> = {}): Record<string, unknown> {
  const mm = new Int16Array([0, 100, 200, 0, 100, 400]);
  return {
    format: 1, x0: 0, y0: 0, cell_m: 1, rows: 2, cols: 3,
    heights_mm: encode(new Uint8Array(mm.buffer)),
    surface_ids: encode(new Uint8Array([0, 0, 1, 0, 1, 1])),
    surfaces: [{ name: "asphalt", rgb: [0.2, 0.2, 0.2], speck: [0.3, 0.3, 0.3] }, { name: "grass", rgb: [0.3, 0.5, 0.2], speck: [0.2, 0.3, 0.1] }],
    obstacles_z: [0.45],
    ...over,
  };
}

describe("parseTerrain", () => {
  it("decodes heights in metres and surface ids", () => {
    const t = parseTerrain(raw());
    expect(Array.from(t.heights)).toEqual([0, 0.1, 0.2, 0, 0.1, 0.4].map((v) => Math.fround(v)));
    expect(Array.from(t.ids)).toEqual([0, 0, 1, 0, 1, 1]);
    expect(t.obstaclesZ).toEqual([0.45]);
  });
  it("refuses what would break the drawing or exhaust memory", () => {
    const bad: Record<string, unknown>[] = [
      { format: 2 },
      { rows: 1 },
      { rows: 2000, cols: MAX_TERRAIN_SAMPLES },
      { cell_m: -1 },
      { heights_mm: "AAAA" },
      { surface_ids: encode(new Uint8Array([0, 0, 9, 0, 0, 0])) },
      { surfaces: [] },
      { surfaces: [{ name: "x", rgb: [2, 0, 0], speck: [0, 0, 0] }] },
      { x0: "1" },
      { obstacles_z: [NaN] },
    ];
    for (const over of bad) expect(() => parseTerrain(raw(over)), JSON.stringify(over)).toThrow(/terrain\.json/);
  });
});

describe("heightAt", () => {
  it("interpolates between samples and holds the edges", () => {
    const t = parseTerrain(raw());
    expect(heightAt(t, 1.5, 0)).toBeCloseTo(0.15);
    expect(heightAt(t, 2, 0.5)).toBeCloseTo(0.3);
    expect(heightAt(t, 99, 99)).toBeCloseTo(0.4);
    expect(heightAt(t, -5, -5)).toBeCloseTo(0);
  });
});

describe("terrainGeometry", () => {
  it("has a vertex per sample at its height, coloured by its surface, and two triangles per cell", () => {
    const g = terrainGeometry(parseTerrain(raw()));
    expect(g.positions.length).toBe(6 * 3);
    expect(g.positions[5 * 3 + 2]).toBeCloseTo(0.4);
    expect(g.indices.length).toBe(2 * 6);
    expect(g.colors[1]).toBeLessThan(0.31); // asphalt: grey, never green
    expect(g.colors[5 * 3 + 1]).toBeGreaterThan(0.29); // grass
  });
});
