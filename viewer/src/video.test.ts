import { describe, expect, it } from "vitest";
import { videoSync } from "./video";

const cam = { fps: 10, start: 2, frames: 50 }; // taken from t = 2 s to t = 6.9 s
const settled = { seeking: false, starting: false };

describe("the head camera's video following the replay", () => {
  it("plays along at the replay's speed and is left alone while it keeps up", () => {
    const cmd = videoSync(cam, { t: 4, playing: true, speed: 2 }, { ...settled, currentTime: 2.1 });
    expect(cmd).toEqual({ run: true, rate: 2, seekTo: null });
  });
  it("is put back on the replay's time when it drifts, with more slack at higher speeds", () => {
    expect(videoSync(cam, { t: 4, playing: true, speed: 1 }, { ...settled, currentTime: 2.5 }).seekTo).toBeCloseTo(2);
    expect(videoSync(cam, { t: 4, playing: true, speed: 4 }, { ...settled, currentTime: 2.5 }).seekTo).toBeNull();
    expect(videoSync(cam, { t: 4, playing: true, speed: 4 }, { ...settled, currentTime: 3.5 }).seekTo).toBeCloseTo(2);
  });
  it("is not sought while play() has not started yet", () => {
    const cmd = videoSync(cam, { t: 4, playing: true, speed: 1 }, { seeking: false, starting: true, currentTime: 0 });
    expect(cmd).toEqual({ run: true, rate: 1, seekTo: null });
  });
  it("holds the frame taken last when the replay is paused", () => {
    const cmd = videoSync(cam, { t: 4.03, playing: false, speed: 1 }, { ...settled, currentTime: 0 });
    expect(cmd.run).toBe(false);
    expect(cmd.rate).toBeNull();
    expect(cmd.seekTo).toBeCloseTo(2.05); // the middle of frame 20
  });
  it("leaves a paused video on the frame it already shows, and waits for a seek under way", () => {
    expect(videoSync(cam, { t: 4.03, playing: false, speed: 1 }, { ...settled, currentTime: 2.05 }).seekTo).toBeNull();
    expect(videoSync(cam, { t: 4.03, playing: false, speed: 1 }, { seeking: true, starting: false, currentTime: 0 }).seekTo).toBeNull();
  });
  it("holds the first frame before the camera started and the last one after it stopped, even while playing", () => {
    const before = videoSync(cam, { t: 1, playing: true, speed: 1 }, { ...settled, currentTime: 3 });
    expect(before.run).toBe(false);
    expect(before.seekTo).toBeCloseTo(0.05);
    const after = videoSync(cam, { t: 6.95, playing: true, speed: 1 }, { ...settled, currentTime: 0 });
    expect(after.run).toBe(false);
    expect(after.seekTo).toBeCloseTo(4.95);
  });
});
