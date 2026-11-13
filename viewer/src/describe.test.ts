import { describe, expect, it } from "vitest";
import { describeEvent, eventTone, hudText } from "./describe";

describe("the text over the 3D view", () => {
  it("gives the time, the pose and what the robot is doing", () => {
    expect(hudText(1.234, { x: 1, y: -2, yaw: 0.5, mode: "walking" }, null)).toBe(
      "t 1.23 s\nx 1.00  y -2.00  yaw 0.50\nwalking",
    );
  });
  it("adds the height when the episode has it", () => {
    expect(hudText(0, { x: 0, y: 0, yaw: 0, mode: "idle", z: 0.789 }, null)).toContain("y 0.00  z 0.79  yaw 0.00");
  });
  it("says how far the robot's belief is from where it really is", () => {
    const text = hudText(0, { x: 0, y: 0, yaw: 0, mode: "idle" }, { x: 0.3, y: 0.4, yaw: 0 });
    expect(text.endsWith("\nbelieves it is 0.50 m away (orange)")).toBe(true);
  });
});

describe("a line for each event", () => {
  const base = { ts: 0, run_id: "r" };
  it("describes perception and safety events", () => {
    expect(
      describeEvent({
        ...base,
        kind: "perception.result",
        target: "shelf_A",
        checks: { "present:red_box": {} },
        anomalies: [],
      }),
    ).toBe("shelf_A: 1 check(s), all passed");
    expect(
      describeEvent({
        ...base,
        kind: "perception.result",
        target: "panel_C",
        checks: { a: {}, b: {} },
        anomalies: ["a"],
      }),
    ).toBe("panel_C: 2 check(s), anomalies: a");
    expect(describeEvent({ ...base, kind: "safety.geofence", pose: { x: 4.5, y: 0.3 } })).toBe(
      "outside the working area at (4.50, 0.30)",
    );
    expect(describeEvent({ ...base, kind: "perception.result", cls: "red_box", found: ["red_box_01"] })).toBe(
      "looking for red_box: found red_box_01",
    );
    expect(describeEvent({ ...base, kind: "perception.result", cls: "red_box", found: [] })).toBe(
      "looking for red_box: nothing found",
    );
  });
  it("describes corrections, missed tags and the simulator's truth", () => {
    expect(
      describeEvent({
        ...base,
        kind: "robot.relocalized",
        at: "dock",
        marker_id: 40,
        correction_m: 0.214,
        correction_rad: 0.04,
      }),
    ).toBe("tag 40 seen at dock: pose corrected by 0.21 m, 2.3°");
    expect(describeEvent({ ...base, kind: "localization.missed", at: "pump_A", markers: [43, 53] })).toBe(
      "at pump_A: tag 43, 53 expected, not seen",
    );
    expect(describeEvent({ ...base, kind: "sim.arrival", miss_m: 0.31, error_m: 0.29 })).toBe(
      "simulator: stopped 0.31 m from the target, belief off by 0.29 m",
    );
  });
  it("are shown as they were written when their fields are not what their kind promises", () => {
    expect(describeEvent({ ...base, kind: "safety.geofence", pose: {} })).toBe('{"pose":{}}');
    expect(describeEvent({ ...base, kind: "state.delta", delta: { robot_pose: { x: "a", y: 1 } } })).toBe(
      '{"delta":{"robot_pose":{"x":"a","y":1}}}',
    );
    expect(describeEvent({ ...base, kind: "perception.result", cls: "box", found: 3 })).toBe('{"cls":"box","found":3}');
    expect(describeEvent({ ...base, kind: "perception.result", target: "shelf", checks: null, anomalies: 4 })).toBe(
      '{"target":"shelf","checks":null,"anomalies":4}',
    );
  });
  it("sets apart the safety monitor's and the operator's events", () => {
    expect(eventTone("safety.geofence")).toBe("safety");
    expect(eventTone("human.request")).toBe("human");
    expect(eventTone("operator.stop")).toBe("human");
    expect(eventTone("skill.end")).toBeNull();
  });
});
