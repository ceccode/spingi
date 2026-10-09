import { strToU8 } from "fflate";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_ROBOT_MODEL, describeEvent, eventTime, normalizePaths, objectsAt, parseEpisodeFiles, parseJsonl, poseAt,
  robotModelUrl, wrapAngle, type Sample,
} from "./episode";

describe("robotModelUrl", () => {
  it("maps the robot profile of the manifest to a bundled model", () => {
    expect(robotModelUrl("unitree_g1")).toBe("/models/g1.glb");
    expect(robotModelUrl("unitree_go2")).toBe("/models/go2.glb");
  });
  it("never builds a path from the episode's text: unknown or odd names fall back to the default", () => {
    for (const bad of ["spot", "../../etc/passwd", "constructor", "__proto__", "", undefined, null, 7, {}]) {
      expect(robotModelUrl(bad)).toBe(DEFAULT_ROBOT_MODEL);
    }
  });
});

const traj = [
  { t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } },
  { t: 2, robot: { x: 2, y: 0, yaw: 3.0, mode: "walking" } },
  { t: 3, robot: { x: 2, y: 1, yaw: -3.0, mode: "idle" } },
];

describe("poseAt", () => {
  it("interpolates linearly between two samples", () => {
    const p = poseAt(traj, 1);
    expect(p.x).toBeCloseTo(1);
    expect(p.y).toBeCloseTo(0);
    expect(p.mode).toBe("idle");
  });
  it("holds the endpoints outside the range", () => {
    expect(poseAt(traj, -5).x).toBe(0);
    expect(poseAt(traj, 99).y).toBe(1);
  });
  it("interpolates yaw along the short side", () => {
    const p = poseAt(traj, 2.5);
    expect(Math.abs(wrapAngle(p.yaw))).toBeGreaterThan(3.0);
  });
  it("handles an empty trajectory", () => {
    expect(poseAt([], 1)).toEqual({ x: 0, y: 0, yaw: 0, mode: "idle" });
  });
});

describe("parsing", () => {
  it("reads jsonl ignoring blank lines", () => {
    expect(parseJsonl<{ a: number }>('{"a":1}\n\n{"a":2}\n')).toEqual([{ a: 1 }, { a: 2 }]);
  });
  it("strips the top-level folder", () => {
    const files = normalizePaths({ "ep-1/manifest.json": strToU8("{}"), "ep-1/frames/a.png": strToU8("x"), "ep-1/frames/": new Uint8Array() });
    expect(Object.keys(files).sort()).toEqual(["frames/a.png", "manifest.json"]);
  });
  it("rejects unknown versions", () => {
    const files = { "manifest.json": strToU8(JSON.stringify({ format_version: "9.9" })) };
    expect(() => parseEpisodeFiles(files)).toThrow(/not supported/);
  });
  it("builds a minimal episode", () => {
    const manifest = { format_version: "0.1", run_id: "r", created_at: "", robot: { model: "fake", adapter: "fake" }, plan_id: "p", status: "success", steps_completed: 1, steps_total: 1, duration_s: 0, files: [] };
    const files = {
      "manifest.json": strToU8(JSON.stringify(manifest)),
      "scene.yaml": strToU8("locations:\n  dock: { pose: { x: 0, y: 0, yaw: 0 } }\n"),
      "plan.yaml": strToU8("id: p\nsteps:\n  - skill: say\n    params: { text: hi }\n"),
      "events.jsonl": strToU8('{"ts": 10, "run_id": "r", "kind": "run.start"}\n{"ts": 12.5, "run_id": "r", "kind": "run.end", "status": "success"}\n'),
      "trajectory.jsonl": strToU8('{"t": 0, "robot": {"x": 0, "y": 0, "yaw": 0, "mode": "idle"}}\n'),
    };
    const ep = parseEpisodeFiles(files);
    expect(ep.scene.locations?.dock?.pose.x).toBe(0);
    expect(ep.plan.steps[0]?.skill).toBe("say");
    expect(eventTime(ep.events[1]!, ep.events)).toBeCloseTo(2.5);
    expect(describeEvent(ep.events[1]!)).toBe("run end: success");
  });
  it("describes perception and safety events", () => {
    const base = { ts: 0, run_id: "r" };
    expect(describeEvent({ ...base, kind: "perception.result", target: "shelf_A", checks: { "present:red_box": {} }, anomalies: [] })).toBe("shelf_A: 1 check(s), all passed");
    expect(describeEvent({ ...base, kind: "perception.result", target: "panel_C", checks: { a: {}, b: {} }, anomalies: ["a"] })).toBe("panel_C: 2 check(s), anomalies: a");
    expect(describeEvent({ ...base, kind: "safety.geofence", pose: { x: 4.5, y: 0.3 } })).toBe("outside the working area at (4.50, 0.30)");
    expect(describeEvent({ ...base, kind: "perception.result", cls: "red_box", found: ["red_box_01"] })).toBe("looking for red_box: found red_box_01");
    expect(describeEvent({ ...base, kind: "perception.result", cls: "red_box", found: [] })).toBe("looking for red_box: nothing found");
  });
});

describe("objectsAt", () => {
  const traj: Sample[] = [
    { t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "idle" }, objects: { box: { x: 1, y: 1, z: 0.9 }, cup: { x: 5, y: 5, z: 0.1 } } },
    { t: 1, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } },
    { t: 2, robot: { x: 0, y: 0, yaw: 0, mode: "idle" }, objects: { box: { x: 2, y: 2, z: 0.95 } } },
  ];
  it("merges the latest known position of each object", () => {
    expect(objectsAt(traj, 1.5)).toEqual({ box: { x: 1, y: 1, z: 0.9 }, cup: { x: 5, y: 5, z: 0.1 } });
    expect(objectsAt(traj, 2)).toEqual({ box: { x: 2, y: 2, z: 0.95 }, cup: { x: 5, y: 5, z: 0.1 } });
  });
  it("is empty before the first sample", () => {
    expect(objectsAt(traj, -1)).toEqual({});
  });
});
