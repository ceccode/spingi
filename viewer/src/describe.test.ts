import { describe, expect, it } from "vitest";
import { hudText } from "./describe";

describe("the text over the 3D view", () => {
  it("gives the time, the pose and what the robot is doing", () => {
    expect(hudText(1.234, { x: 1, y: -2, yaw: 0.5, mode: "walking" }, null)).toBe("t 1.23 s\nx 1.00  y -2.00  yaw 0.50\nwalking");
  });
  it("adds the height when the episode has it", () => {
    expect(hudText(0, { x: 0, y: 0, yaw: 0, mode: "idle", z: 0.789 }, null)).toContain("y 0.00  z 0.79  yaw 0.00");
  });
  it("says how far the robot's belief is from where it really is", () => {
    const text = hudText(0, { x: 0, y: 0, yaw: 0, mode: "idle" }, { x: 0.3, y: 0.4, yaw: 0 });
    expect(text.endsWith("\nbelieves it is 0.50 m away (orange)")).toBe(true);
  });
});
