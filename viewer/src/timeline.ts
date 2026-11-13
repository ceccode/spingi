/** Questions about an episode at one time of the replay: where the robot was, which events had happened, which
 *  frames it had taken. No dependency on rendering. */
import type { Episode, Event, Pose2D, Position, RobotPose, Sample } from "./episode";

export function wrapAngle(a: number): number {
  return ((((a + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
}

/** The two samples around time t and how far between them t is (0 up to, not including, 1); outside the range, the
 *  end sample twice. */
type Bracket = [Sample, Sample, number];

function bracket(trajectory: Sample[], t: number): Bracket | null {
  const first = trajectory[0];
  if (!first) return null;
  if (t <= first.t) return [first, first, 0];
  const last = trajectory[trajectory.length - 1]!;
  if (t >= last.t) return [last, last, 0];
  let lo = 0;
  let hi = trajectory.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (trajectory[mid]!.t <= t) lo = mid;
    else hi = mid;
  }
  const a = trajectory[lo]!;
  const b = trajectory[hi]!;
  return [a, b, b.t === a.t ? 0 : (t - a.t) / (b.t - a.t)];
}

const lerp = (a: number | undefined, b: number | undefined, u: number): number | undefined =>
  a === undefined || b === undefined ? a : a + (b - a) * u;

/** Everything the replay draws at one time. */
export interface Snapshot {
  pose: RobotPose;
  estimate: Pose2D | null; // null while the robot's belief matched the truth
  joints: Record<string, number> | null; // null when the episode has none (kinematic runs: the robot stands)
  objects: Record<string, Position>;
}

/** The episode at time t, from one look at the trajectory. */
export function stateAt(trajectory: Sample[], t: number): Snapshot {
  const br = bracket(trajectory, t);
  return {
    pose: br ? poseIn(br) : { ...NOWHERE },
    estimate: br ? estimateIn(br) : null,
    joints: br ? jointsIn(br) : null,
    objects: objectsAt(trajectory, t),
  };
}

const NOWHERE: RobotPose = { x: 0, y: 0, yaw: 0, mode: "idle" }; // an episode without a trajectory

/** Pose linearly interpolated at time t; outside the range it holds the first or last sample. */
export function poseAt(trajectory: Sample[], t: number): RobotPose {
  const br = bracket(trajectory, t);
  return br ? poseIn(br) : { ...NOWHERE };
}

function poseIn([a, b, u]: Bracket): RobotPose {
  if (a === b) return { ...a.robot };
  const pose: RobotPose = {
    x: a.robot.x + (b.robot.x - a.robot.x) * u,
    y: a.robot.y + (b.robot.y - a.robot.y) * u,
    yaw: a.robot.yaw + wrapAngle(b.robot.yaw - a.robot.yaw) * u,
    mode: a.robot.mode,
  };
  const z = lerp(a.robot.z, b.robot.z, u), roll = lerp(a.robot.roll, b.robot.roll, u), pitch = lerp(a.robot.pitch, b.robot.pitch, u);
  if (z !== undefined) Object.assign(pose, { z, roll: roll ?? 0, pitch: pitch ?? 0 });
  return pose;
}

/** Where the robot believed it was at time t, interpolated; null while its belief matched the truth. */
export function estimateAt(trajectory: Sample[], t: number): Pose2D | null {
  const br = bracket(trajectory, t);
  return br ? estimateIn(br) : null;
}

function estimateIn([a, b, u]: Bracket): Pose2D | null {
  if (!a.estimate) return null;
  if (!b.estimate || a === b) return { ...a.estimate };
  return {
    x: a.estimate.x + (b.estimate.x - a.estimate.x) * u,
    y: a.estimate.y + (b.estimate.y - a.estimate.y) * u,
    yaw: a.estimate.yaw + wrapAngle(b.estimate.yaw - a.estimate.yaw) * u,
  };
}

/** Joint angles at time t, interpolated; null when the episode has none (kinematic runs: the robot stands). */
export function jointsAt(trajectory: Sample[], t: number): Record<string, number> | null {
  const br = bracket(trajectory, t);
  return br ? jointsIn(br) : null;
}

function jointsIn([a, b, u]: Bracket): Record<string, number> | null {
  if (!a.joints) return null;
  if (!b.joints || a === b) return a.joints;
  const out: Record<string, number> = {};
  for (const [name, qa] of Object.entries(a.joints)) {
    const qb = b.joints[name];
    out[name] = qb === undefined ? qa : qa + (qb - qa) * u;
  }
  return out;
}

/** Object positions at time t: the latest sample at or before t that carries `objects`, merged over earlier ones. */
export function objectsAt(trajectory: Sample[], t: number): Record<string, Position> {
  const out: Record<string, Position> = {};
  for (const s of trajectory) {
    if (s.t > t) break;
    if (s.objects) Object.assign(out, s.objects);
  }
  return out;
}

/** Time of an event on the trajectory axis: sim_t if present, otherwise wall-clock time since run.start. */
export function eventTime(ev: Event, events: Event[]): number {
  if (typeof ev.sim_t === "number") return ev.sim_t;
  const t0 = events[0]?.ts ?? ev.ts;
  return ev.ts - t0;
}

/** The time of every event, in the order of the log. */
export function eventTimes(events: Event[]): number[] {
  return events.map((e) => eventTime(e, events));
}

/** A camera frame of the episode and the replay time it is shown from. */
export interface TimedFrame { id: string; t: number }

/** The frames in the order they are shown, each from the time of the event that names it. An episode whose events
 *  name none of its frames shows them all from the start. */
export function frameTimeline(ep: Pick<Episode, "events" | "frames">): TimedFrame[] {
  const named: TimedFrame[] = [];
  for (const e of ep.events) {
    const id = e.frame_id;
    if (typeof id === "string" && ep.frames.has(id)) named.push({ id, t: eventTime(e, ep.events) });
  }
  return named.length > 0 ? named : [...ep.frames.keys()].map((id) => ({ id, t: 0 }));
}

/** How many of `times` are at or before t, and the index of the last one that is (-1 when none). The times need not
 *  be sorted. As t moves, the ones reached only grow or only shrink: the same count means the same ones. */
export function reached(times: number[], t: number): { count: number; last: number } {
  let count = 0;
  let last = -1;
  times.forEach((time, i) => {
    if (time > t) return;
    count++;
    last = i;
  });
  return { count, last };
}

export function episodeDuration(ep: Episode): number {
  const last = ep.trajectory[ep.trajectory.length - 1];
  const lastEvent = ep.events[ep.events.length - 1];
  return Math.max(last?.t ?? 0, lastEvent ? eventTime(lastEvent, ep.events) : 0, 0.1);
}
