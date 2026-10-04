import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { strToU8, zipSync } from "fflate";
import { describeEvent, loadEpisodeFromZip, manifestRows, MAX_UNZIPPED_BYTES, safeEpisodeUrl, type Episode } from "./episode";

const EVIL = '<img src=x onerror="alert(1)">';

describe("untrusted episode content", () => {
  it("never reaches the page through innerHTML", () => {
    const sources = readdirSync("src").filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"));
    expect(sources.length).toBeGreaterThanOrEqual(4);
    for (const file of sources.map((f) => `src/${f}`)) {
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
    expect(safeEpisodeUrl("http://example.org/e.zip", base)).toBeNull();  // http only from our own origin
    expect(safeEpisodeUrl("http://localhost:5173/s.zip", "http://localhost:5173/")).toBe("http://localhost:5173/s.zip");
  });
});

describe("malformed episodes", () => {
  it("refuses trajectories with missing or non-finite values", async () => {
    const { checkTrajectory } = await import("./episode");
    expect(() => checkTrajectory([{ t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } }])).not.toThrow();
    expect(() => checkTrajectory([{ t: 0 } as never])).toThrow(/line 1/);
    expect(() => checkTrajectory([{ t: Number.NaN, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } }])).toThrow();
  });
  it("stops reading a response that grows beyond the cap", async () => {
    const { readCapped } = await import("./episode");
    const body = new ReadableStream({ start(c) { c.enqueue(new Uint8Array(600)); c.enqueue(new Uint8Array(600)); c.close(); } });
    await expect(readCapped(new Response(body), 1000)).rejects.toThrow(/refused/);
    const small = new ReadableStream({ start(c) { c.enqueue(new Uint8Array(10)); c.close(); } });
    expect((await readCapped(new Response(small), 1000)).byteLength).toBe(10);
  });
});
