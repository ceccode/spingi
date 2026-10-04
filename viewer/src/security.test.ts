import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { strToU8, zipSync } from "fflate";
import { describeEvent, loadEpisodeFromZip, manifestRows, MAX_UNZIPPED_BYTES, safeEpisodeUrl, type Episode } from "./episode";

const EVIL = '<img src=x onerror="alert(1)">';

describe("untrusted episode content", () => {
  it("never reaches the page through innerHTML", () => {
    for (const file of ["src/main.ts", "src/world.ts"]) {
      const source = readFileSync(file, "utf8");
      expect(source, file).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
    }
  });
  it("is passed through as plain text by the describers", () => {
    expect(describeEvent({ ts: 0, run_id: "r", kind: "say", text: EVIL })).toBe(`"${EVIL}"`);
    const ep = {
      manifest: { format_version: "0.1", run_id: "r", created_at: EVIL, robot: { model: EVIL, adapter: "a" },
        plan_id: EVIL, status: "success", steps_completed: 1, steps_total: 1, duration_s: "nan", files: [] },
      scene: {}, plan: { id: "p", description: EVIL, steps: [] }, events: [], trajectory: [], frames: new Map(),
    } as unknown as Episode;
    const rows = Object.fromEntries(manifestRows(ep));
    expect(rows.Plan).toBe(EVIL);
    expect(rows.Duration).toBe("unknown");
  });
});

describe("episode loading limits", () => {
  it("refuses archives that expand beyond the limit (zip bomb)", () => {
    const zeros = new Uint8Array(4 * 1024 * 1024);  // compresses to a few KB
    const bomb = zipSync({ "a.bin": zeros, "b.bin": zeros }, { level: 9 });
    expect(bomb.byteLength).toBeLessThan(100 * 1024);
    const limits = { zipBytes: 1024 * 1024, unzippedBytes: 6 * 1024 * 1024 };
    expect(() => loadEpisodeFromZip(bomb.buffer as ArrayBuffer, limits)).toThrow(/refused/);
    expect(MAX_UNZIPPED_BYTES).toBe(500 * 1024 * 1024);
  });
  it("still loads a normal archive", () => {
    const zip = zipSync({ "ep/manifest.json": strToU8(JSON.stringify({ format_version: "9" })) });
    expect(() => loadEpisodeFromZip(zip.buffer as ArrayBuffer)).toThrow(/not supported/);
  });
  it("only fetches http and https URLs", () => {
    const base = "https://spingi-viewer.netlify.app/";
    expect(safeEpisodeUrl("/samples/a.zip", base)).toBe("https://spingi-viewer.netlify.app/samples/a.zip");
    expect(safeEpisodeUrl("https://example.org/e.zip", base)).toBe("https://example.org/e.zip");
    expect(safeEpisodeUrl("javascript:alert(1)", base)).toBeNull();
    expect(safeEpisodeUrl("data:application/zip;base64,AAAA", base)).toBeNull();
    expect(safeEpisodeUrl("file:///etc/passwd", base)).toBeNull();
  });
});
