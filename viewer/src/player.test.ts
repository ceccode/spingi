import { describe, expect, it } from "vitest";
import { Player } from "./player";

describe("Player", () => {
  it("advances only while playing and stops at the end", () => {
    const p = new Player();
    p.reset(2);
    p.advance(1);
    expect(p.t).toBe(0);
    p.toggle();
    p.advance(1.5);
    expect(p.t).toBeCloseTo(1.5);
    p.advance(5);
    expect(p.t).toBe(2);
    expect(p.playing).toBe(false);
  });
  it("restarts from zero when play is pressed at the end", () => {
    const p = new Player();
    p.reset(1);
    p.seek(1);
    p.toggle();
    expect(p.t).toBe(0);
    expect(p.playing).toBe(true);
  });
  it("respects the speed", () => {
    const p = new Player();
    p.reset(10); p.speed = 4; p.toggle(); p.advance(1);
    expect(p.t).toBeCloseTo(4);
  });
});
