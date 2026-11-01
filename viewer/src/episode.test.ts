import { strToU8 } from "fflate";
import { describe, expect, it } from "vitest";
import {
  checkTrajectory, DEFAULT_ROBOT_MODEL, describeEvent, estimateAt, eventTime, jointsAt, landmarksOf, normalizePaths,
  objectsAt, parseEpisodeFiles, parseJsonl, poseAt, robotJointsUrl, robotModelUrl, wrapAngle, type Sample,
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

describe("physics episodes", () => {
  const walk: Sample[] = [
    { t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "walking", z: 0.3, roll: 0, pitch: 0.1 }, joints: { knee: 0.2, hip: 1.0 } },
    { t: 1, robot: { x: 1, y: 0, yaw: 0, mode: "walking", z: 0.5, roll: 0.2, pitch: 0.3 }, joints: { knee: 0.6, hip: 1.0 } },
  ];
  it("interpolates the base height and tilt with the pose", () => {
    const p = poseAt(walk, 0.5);
    expect(p.z).toBeCloseTo(0.4);
    expect(p.roll).toBeCloseTo(0.1);
    expect(p.pitch).toBeCloseTo(0.2);
    expect(poseAt(traj, 1).z).toBeUndefined(); // kinematic episodes on a flat floor have none
  });
  it("interpolates joint angles, and has none for kinematic episodes", () => {
    expect(jointsAt(walk, 0.25)!.knee).toBeCloseTo(0.3);
    expect(jointsAt(walk, 5)!.knee).toBeCloseTo(0.6);
    expect(jointsAt(traj, 1)).toBeNull();
  });
  it("refuses non-finite heights and joint angles, and too many joints", () => {
    const bad = (s: Partial<Sample>): Sample[] => [{ ...walk[0]!, ...s } as Sample];
    expect(() => checkTrajectory(bad({ robot: { ...walk[0]!.robot, z: NaN } }))).toThrow(/malformed/);
    expect(() => checkTrajectory(bad({ joints: { knee: Infinity } }))).toThrow(/malformed/);
    const many = Object.fromEntries(Array.from({ length: 65 }, (_, i) => [`j${i}`, 0]));
    expect(() => checkTrajectory(bad({ joints: many }))).toThrow(/malformed/);
    expect(checkTrajectory(walk)).toHaveLength(2);
  });
  it("finds the joint table next to the model", () => {
    expect(robotJointsUrl(robotModelUrl("unitree_go2"))).toBe("/models/go2.joints.json");
  });
});

describe("what the robot believed (drifting odometry)", () => {
  const drift: Sample[] = [
    { t: 0, robot: { x: 0, y: 0, yaw: 0, mode: "idle" } },
    { t: 1, robot: { x: 1, y: 0, yaw: 0, mode: "walking" }, estimate: { x: 1, y: 0.2, yaw: 0.1 } },
    { t: 2, robot: { x: 2, y: 0, yaw: 0, mode: "walking" }, estimate: { x: 2, y: 0.4, yaw: 0.2 } },
  ];
  it("interpolates the believed pose, and has none while the belief was the truth", () => {
    expect(estimateAt(drift, 0.5)).toBeNull();
    const e = estimateAt(drift, 1.5)!;
    expect(e.y).toBeCloseTo(0.3);
    expect(e.yaw).toBeCloseTo(0.15);
    expect(estimateAt(drift, 9)!.x).toBe(2);
  });
  it("refuses a believed pose that is not three numbers", () => {
    expect(() => checkTrajectory([{ ...drift[1]!, estimate: { x: 1, y: NaN, yaw: 0 } }])).toThrow(/malformed/);
    expect(() => checkTrajectory([{ ...drift[1]!, estimate: "here" as unknown as Sample["estimate"] }])).toThrow(/malformed/);
  });
  it("reads the scene's landmarks with their defaults and skips malformed ones", () => {
    const lms = landmarksOf({
      landmarks: [
        { marker_id: 40, x: 2.6, y: -1.3, facing: 2.68 },
        { marker_id: 41, x: 1, y: 1, facing: 0, size_m: 0.3, height_m: 0.45, post: false },
        { marker_id: "x", x: 0, y: 0 }, { marker_id: 42, x: "1", y: 0 }, { marker_id: 43, x: 0, y: 0, size_m: 50 }, null,
      ],
    });
    expect(lms.map((l) => l.markerId)).toEqual([40, 41]);
    expect(lms[0]).toMatchObject({ height: 0.6, size: 0.2, post: true });
    expect(lms[1]).toMatchObject({ height: 0.45, size: 0.3, post: false });
    expect(landmarksOf({ landmarks: "nope" })).toEqual([]);
  });
  it("describes corrections, missed tags and the simulator's truth", () => {
    const base = { ts: 0, run_id: "r" };
    expect(describeEvent({ ...base, kind: "robot.relocalized", at: "dock", marker_id: 40, correction_m: 0.214, correction_rad: 0.04 }))
      .toBe("tag 40 seen at dock: pose corrected by 0.21 m, 2.3°");
    expect(describeEvent({ ...base, kind: "localization.missed", at: "pump_A", markers: [43, 53] })).toBe("at pump_A: tag 43, 53 expected, not seen");
    expect(describeEvent({ ...base, kind: "sim.arrival", miss_m: 0.31, error_m: 0.29 })).toBe("simulator: stopped 0.31 m from the target, belief off by 0.29 m");
  });
  it("shows JPEG frames from a noisy camera as well as PNG ones", () => {
    const manifest = { format_version: "0.1", run_id: "r", created_at: "", robot: { model: "unitree_go2", adapter: "sim_mujoco" }, plan_id: "p", status: "success", steps_completed: 0, steps_total: 0, duration_s: 0, files: [] };
    const ep = parseEpisodeFiles({
      "manifest.json": strToU8(JSON.stringify(manifest)), "scene.yaml": strToU8("{}"), "plan.yaml": strToU8("id: p\nsteps: []\n"),
      "events.jsonl": strToU8(""), "frames/sim-1.jpg": strToU8("j"), "frames/sim-2.png": strToU8("p"), "frames/notes.txt": strToU8("t"),
    });
    expect([...ep.frames.keys()].sort()).toEqual(["sim-1", "sim-2"]);
    expect(ep.frames.get("sim-1")!.type).toBe("image/jpeg");
  });
});
