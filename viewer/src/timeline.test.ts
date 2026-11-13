import { describe, expect, it } from "vitest";
import type { Sample } from "./episode";
import {
  estimateAt,
  eventTimes,
  frameTimeline,
  jointsAt,
  objectsAt,
  poseAt,
  reached,
  stateAt,
  wrapAngle,
} from "./timeline";

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

describe("objectsAt", () => {
  const traj: Sample[] = [
    {
      t: 0,
      robot: { x: 0, y: 0, yaw: 0, mode: "idle" },
      objects: { box: { x: 1, y: 1, z: 0.9 }, cup: { x: 5, y: 5, z: 0.1 } },
    },
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
    {
      t: 0,
      robot: { x: 0, y: 0, yaw: 0, mode: "walking", z: 0.3, roll: 0, pitch: 0.1 },
      joints: { knee: 0.2, hip: 1.0 },
    },
    {
      t: 1,
      robot: { x: 1, y: 0, yaw: 0, mode: "walking", z: 0.5, roll: 0.2, pitch: 0.3 },
      joints: { knee: 0.6, hip: 1.0 },
    },
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
});

describe("the episode at one time", () => {
  it("has the pose, the belief, the joints and the objects that the single questions give", () => {
    const samples: Sample[] = [
      {
        t: 0,
        robot: { x: 0, y: 0, yaw: 0, mode: "walking", z: 0.7 },
        estimate: { x: 0.2, y: 0, yaw: 0 },
        joints: { knee: 0 },
        objects: { box: { x: 1, y: 1, z: 0.1 } },
      },
      {
        t: 2,
        robot: { x: 2, y: 0, yaw: 0, mode: "idle", z: 0.9 },
        estimate: { x: 2.4, y: 0, yaw: 0 },
        joints: { knee: 1 },
      },
    ];
    const state = stateAt(samples, 1);
    expect(state).toEqual({
      pose: poseAt(samples, 1),
      estimate: estimateAt(samples, 1),
      joints: jointsAt(samples, 1),
      objects: objectsAt(samples, 1),
    });
    expect(state.pose).toMatchObject({ x: 1, z: 0.8, mode: "walking" });
    expect(state.estimate?.x).toBeCloseTo(1.3);
    expect(state.joints?.knee).toBeCloseTo(0.5);
    expect(state.objects.box?.x).toBe(1);
  });
  it("stands idle at the origin, with nothing else, when the episode has no trajectory", () => {
    expect(stateAt([], 3)).toEqual({
      pose: { x: 0, y: 0, yaw: 0, mode: "idle" },
      estimate: null,
      joints: null,
      objects: {},
    });
  });
});

describe("the event list and the frames along the replay", () => {
  const events = [
    { ts: 10, run_id: "r", kind: "run.start", sim_t: 0 },
    { ts: 11, run_id: "r", kind: "perception.result", sim_t: 4, frame_id: "f2" },
    { ts: 12, run_id: "r", kind: "perception.result", sim_t: 6, frame_id: "gone" },
    { ts: 13, run_id: "r", kind: "perception.result", sim_t: 9, frame_id: "f1" },
  ];
  const blob = new Blob([]);
  it("times every event on the replay's axis", () => {
    expect(eventTimes(events)).toEqual([0, 4, 6, 9]);
  });
  it("shows each frame from the event that names it, and skips events whose frame is not in the episode", () => {
    const frames = new Map([
      ["f1", blob],
      ["f2", blob],
    ]);
    expect(frameTimeline({ events, frames })).toEqual([
      { id: "f2", t: 4 },
      { id: "f1", t: 9 },
    ]);
  });
  it("shows every frame from the start when no event names one", () => {
    const frames = new Map([
      ["a", blob],
      ["b", blob],
    ]);
    expect(frameTimeline({ events: events.slice(0, 1), frames })).toEqual([
      { id: "a", t: 0 },
      { id: "b", t: 0 },
    ]);
    expect(frameTimeline({ events, frames: new Map() })).toEqual([]);
  });
  it("counts what the replay has reached and names the last of it", () => {
    expect(reached([0, 4, 6, 9], -1)).toEqual({ count: 0, last: -1 });
    expect(reached([0, 4, 6, 9], 4)).toEqual({ count: 2, last: 1 });
    expect(reached([0, 4, 6, 9], 99)).toEqual({ count: 4, last: 3 });
    expect(reached([], 1)).toEqual({ count: 0, last: -1 });
  });
  it("takes the last in the list, not the latest in time, when the times are out of order", () => {
    expect(reached([0, 5, 3], 4)).toEqual({ count: 2, last: 2 });
  });
});
