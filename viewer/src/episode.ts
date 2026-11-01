/** Reading an episode (docs/episode-format.md). No dependency on rendering. */
import { strFromU8, unzipSync } from "fflate";
import YAML from "yaml";
import { parseTerrain, type Terrain } from "./terrain";

export interface Pose2D { x: number; y: number; yaw: number }
export interface Manifest {
  format_version: string; run_id: string; created_at: string;
  robot: { model: string; adapter: string };
  plan_id: string; status: string; steps_completed: number; steps_total: number;
  duration_s: number; sim_time_s?: number | null; sample_rate_hz?: number | null; files: string[];
}
/** Base height and tilt: present when the robot left the flat floor (outdoor ground, physics walking). */
export interface BaseLift { z?: number; roll?: number; pitch?: number }
export interface Sample {
  t: number; robot: Pose2D & BaseLift & { mode: string }; battery_pct?: number;
  estimate?: Pose2D; // where the robot believed it was, when its odometry had drifted (ADR-0016)
  objects?: Record<string, { x: number; y: number; z: number }>;
  joints?: Record<string, number>; // joint angles in radians (physics walking)
}
export interface Event { ts: number; run_id: string; kind: string; sim_t?: number; [k: string]: unknown }
export interface Scene {
  robot?: { pose?: Pose2D; battery_pct?: number };
  locations?: Record<string, { pose: Pose2D; tolerance_m?: number }>;
  obstacles?: { x: number; y: number; w?: number; d?: number; h?: number; color?: string }[];
  objects?: Record<string, { cls: string; pose?: { x: number; y: number; z: number }; size?: number[] }>;
  landmarks?: unknown; // checked by `landmarksOf` before anything is drawn
}
/** A tag at a known place (scene.yaml `landmarks`): x, y on the ground, the direction its face looks, its height. */
export interface Landmark { markerId: number; x: number; y: number; facing: number; height: number; size: number; post: boolean }
export interface Plan { id: string; description?: string; steps: { skill: string; params: Record<string, unknown> }[] }
export interface Episode {
  manifest: Manifest; scene: Scene; plan: Plan; events: Event[]; trajectory: Sample[];
  frames: Map<string, Blob>;
  terrain: Terrain | null; // outdoor ground; null on a flat floor
}

export const SUPPORTED_FORMAT = "0.1";
/** The GLB drawn for each robot profile (runtime/spingi/robots.py). The model name comes from the episode, so it is
 *  looked up in this table and never used as a path: an unknown robot is drawn as the G1. */
export const ROBOT_MODELS: Record<string, string> = { unitree_g1: "/models/g1.glb", unitree_go2: "/models/go2.glb" };
export const DEFAULT_ROBOT_MODEL = ROBOT_MODELS.unitree_g1!;
export function robotModelUrl(model: unknown): string {
  return (typeof model === "string" && Object.hasOwn(ROBOT_MODELS, model) ? ROBOT_MODELS[model] : undefined) ?? DEFAULT_ROBOT_MODEL;
}
/** The joint table next to each GLB (which node each joint turns, about which axis, the standing angles). */
export function robotJointsUrl(modelUrl: string): string {
  return modelUrl.replace(/\.glb$/, ".joints.json");
}
export const MAX_JOINTS = 64;
export const MAX_LANDMARKS = 256;
/** Episodes come from strangers too: refuse archives that would exhaust the browser's memory (zip bombs). */
export const MAX_ZIP_BYTES = 200 * 1024 * 1024;
export const MAX_UNZIPPED_BYTES = 500 * 1024 * 1024;

export function parseJsonl<T>(text: string): T[] {
  return text.split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l) as T);
}

/** Strips the optional top-level folder: the root is wherever manifest.json lives. */
export function normalizePaths(files: Record<string, Uint8Array>): Record<string, Uint8Array> {
  const manifestPath = Object.keys(files).find((p) => p.endsWith("manifest.json"));
  if (!manifestPath) throw new Error("manifest.json not found in episode");
  const prefix = manifestPath.slice(0, manifestPath.length - "manifest.json".length);
  const out: Record<string, Uint8Array> = {};
  for (const [p, data] of Object.entries(files)) {
    if (!p.startsWith(prefix) || p.endsWith("/")) continue;
    out[p.slice(prefix.length)] = data;
  }
  return out;
}

export function parseEpisodeFiles(raw: Record<string, Uint8Array>): Episode {
  const files = normalizePaths(raw);
  const text = (name: string): string => {
    const data = files[name];
    if (!data) throw new Error(`missing file in episode: ${name}`);
    return strFromU8(data);
  };
  const manifest = JSON.parse(text("manifest.json")) as Manifest;
  if (typeof manifest !== "object" || manifest === null) throw new Error("manifest.json is not an object");
  if (manifest.format_version !== SUPPORTED_FORMAT) {
    throw new Error(`episode format ${manifest.format_version} not supported (expected ${SUPPORTED_FORMAT})`);
  }
  const frames = new Map<string, Blob>();
  for (const [p, data] of Object.entries(files)) {
    // PNG from a clean render, JPEG from a noisy camera (as the Go2 streams it); anything else is not shown.
    const m = /^frames\/([^/]+)\.(png|jpe?g)$/i.exec(p);
    if (m) frames.set(m[1]!, new Blob([data as BlobPart], { type: m[2]!.toLowerCase() === "png" ? "image/png" : "image/jpeg" }));
  }
  return {
    manifest,
    scene: YAML.parse(text("scene.yaml")) as Scene,
    plan: YAML.parse(text("plan.yaml")) as Plan,
    events: parseJsonl<Event>(text("events.jsonl")),
    trajectory: files["trajectory.jsonl"] ? checkTrajectory(parseJsonl<Sample>(text("trajectory.jsonl"))) : [],
    frames,
    terrain: files["terrain.json"] ? parseTerrain(JSON.parse(text("terrain.json"))) : null,
  };
}

/** A malformed trajectory is refused at load time instead of breaking the replay loop later. */
export function checkTrajectory(samples: Sample[]): Sample[] {
  samples.forEach((s, i) => {
    const r = s?.robot;
    const optional = (v: unknown): boolean => v === undefined || Number.isFinite(v);
    let ok = Number.isFinite(s?.t) && r && [r.x, r.y, r.yaw].every((v) => Number.isFinite(v));
    ok = ok && optional(r.z) && optional(r.roll) && optional(r.pitch);
    const e = s.estimate;
    ok = ok && (e === undefined || (typeof e === "object" && e !== null && [e.x, e.y, e.yaw].every((v) => Number.isFinite(v))));
    if (ok && s.joints !== undefined) {
      const values = typeof s.joints === "object" && s.joints !== null ? Object.values(s.joints) : null;
      ok = values !== null && values.length <= MAX_JOINTS && values.every((v) => Number.isFinite(v));
    }
    if (!ok) throw new Error(`malformed trajectory line ${i + 1}`);
  });
  return samples;
}

export function loadEpisodeFromZip(
  buf: ArrayBuffer,
  limits: { zipBytes: number; unzippedBytes: number } = { zipBytes: MAX_ZIP_BYTES, unzippedBytes: MAX_UNZIPPED_BYTES },
): Episode {
  if (buf.byteLength > limits.zipBytes) {
    throw new Error(`episode archive too large (${mb(buf.byteLength)} MB, limit ${mb(limits.zipBytes)} MB)`);
  }
  let total = 0;
  const files = unzipSync(new Uint8Array(buf), {
    filter: (f) => {  // checked against the sizes declared in the archive, before anything is inflated
      total += f.originalSize;
      if (total > limits.unzippedBytes) throw new Error(`episode expands beyond ${mb(limits.unzippedBytes)} MB, refused`);
      return true;
    },
  });
  return parseEpisodeFiles(files);
}

/** `?url=` may fetch https URLs anywhere, and http only from the viewer's own origin (local development). */
export function safeEpisodeUrl(raw: string, base: string): string | null {
  try {
    const url = new URL(raw, base);
    if (url.protocol === "https:") return url.href;
    if (url.protocol === "http:" && url.origin === new URL(base).origin) return url.href;
    return null;
  } catch {
    return null;
  }
}

/** Reads a response body, giving up as soon as it exceeds `maxBytes` (servers may omit or lie in Content-Length). */
export async function readCapped(res: Response, maxBytes: number): Promise<ArrayBuffer> {
  if (!res.body) return res.arrayBuffer();
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error(`episode archive larger than ${mb(maxBytes)} MB, refused`);
    }
    chunks.push(value);
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out.buffer;
}

function mb(bytes: number): number {
  return Math.round(bytes / 1024 / 1024);
}

export function wrapAngle(a: number): number {
  return ((((a + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
}

/** The two samples around time t and how far between them t is (0..1); outside the range, the end sample twice. */
function bracket(trajectory: Sample[], t: number): [Sample, Sample, number] | null {
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

/** Pose linearly interpolated at time t; outside the range it holds the first or last sample. */
export function poseAt(trajectory: Sample[], t: number): Pose2D & BaseLift & { mode: string } {
  const br = bracket(trajectory, t);
  if (!br) return { x: 0, y: 0, yaw: 0, mode: "idle" };
  const [a, b, u] = br;
  if (a === b) return { ...a.robot };
  const pose: Pose2D & BaseLift & { mode: string } = {
    x: a.robot.x + (b.robot.x - a.robot.x) * u,
    y: a.robot.y + (b.robot.y - a.robot.y) * u,
    yaw: a.robot.yaw + wrapAngle(b.robot.yaw - a.robot.yaw) * u,
    mode: u < 1 ? a.robot.mode : b.robot.mode,
  };
  const z = lerp(a.robot.z, b.robot.z, u), roll = lerp(a.robot.roll, b.robot.roll, u), pitch = lerp(a.robot.pitch, b.robot.pitch, u);
  if (z !== undefined) Object.assign(pose, { z, roll: roll ?? 0, pitch: pitch ?? 0 });
  return pose;
}

/** Where the robot believed it was at time t, interpolated; null while its belief matched the truth. */
export function estimateAt(trajectory: Sample[], t: number): Pose2D | null {
  const br = bracket(trajectory, t);
  if (!br) return null;
  const [a, b, u] = br;
  if (!a.estimate) return null;
  if (!b.estimate || a === b) return { ...a.estimate };
  return {
    x: a.estimate.x + (b.estimate.x - a.estimate.x) * u,
    y: a.estimate.y + (b.estimate.y - a.estimate.y) * u,
    yaw: a.estimate.yaw + wrapAngle(b.estimate.yaw - a.estimate.yaw) * u,
  };
}

/** The scene's landmarks, checked: a malformed entry is skipped, never drawn half-way. */
export function landmarksOf(scene: Scene): Landmark[] {
  if (!Array.isArray(scene.landmarks)) return [];
  const out: Landmark[] = [];
  for (const raw of scene.landmarks.slice(0, MAX_LANDMARKS)) {
    const l = raw as Record<string, unknown>;
    const num = (v: unknown, fallback: number): number => (v === undefined ? fallback : typeof v === "number" && Number.isFinite(v) ? v : NaN);
    const lm = {
      markerId: num(l?.marker_id, NaN), x: num(l?.x, NaN), y: num(l?.y, NaN), facing: num(l?.facing, 0),
      height: num(l?.height_m, 0.6), size: num(l?.size_m, 0.2), post: l?.post !== false,
    };
    const ok = Number.isInteger(lm.markerId) && [lm.x, lm.y, lm.facing].every(Number.isFinite)
      && lm.height > 0 && lm.height <= 5 && lm.size > 0 && lm.size <= 1;
    if (ok) out.push(lm);
  }
  return out;
}

/** Joint angles at time t, interpolated; null when the episode has none (kinematic runs: the robot stands). */
export function jointsAt(trajectory: Sample[], t: number): Record<string, number> | null {
  const br = bracket(trajectory, t);
  if (!br) return null;
  const [a, b, u] = br;
  if (!a.joints) return null;
  if (!b.joints || a === b) return a.joints;
  const out: Record<string, number> = {};
  for (const [name, qa] of Object.entries(a.joints)) {
    const qb = b.joints[name];
    out[name] = qb === undefined ? qa : qa + (qb - qa) * u;
  }
  return out;
}

/** The rows of the episode card, as plain strings. */
export function manifestRows(ep: Episode): [string, string][] {
  const m = ep.manifest;
  const seconds = Number(m.sim_time_s ?? m.duration_s);
  return [
    ["Outcome", `${m.status} · ${m.steps_completed}/${m.steps_total} steps`],
    ["Robot", `${m.robot?.model} · ${m.robot?.adapter}`],
    ["Duration", Number.isFinite(seconds) ? `${seconds.toFixed(1)} s simulated` : "unknown"],
    ["Created", String(m.created_at ?? "").replace("T", " ").replace("Z", " UTC")],
    ["Plan", String(ep.plan.description ?? ep.plan.id)],
  ];
}

/** Object positions at time t: the latest sample at or before t that carries `objects`, merged over earlier ones. */
export function objectsAt(trajectory: Sample[], t: number): Record<string, { x: number; y: number; z: number }> {
  const out: Record<string, { x: number; y: number; z: number }> = {};
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

export function episodeDuration(ep: Episode): number {
  const last = ep.trajectory[ep.trajectory.length - 1];
  const lastEvent = ep.events[ep.events.length - 1];
  return Math.max(last?.t ?? 0, lastEvent ? eventTime(lastEvent, ep.events) : 0, 0.1);
}

export function describeEvent(ev: Event): string {
  const d = ev as Record<string, unknown>;
  switch (ev.kind) {
    case "step.start": return `step ${d.index}: ${d.skill} ${JSON.stringify(d.params)}`;
    case "skill.end": return `${d.skill} → ${d.outcome}${d.reason ? ` (${d.reason})` : ""}`;
    case "say": return `"${d.text}"`;
    case "human.request": return `operator: ${d.skill} — ${d.reason}${Array.isArray(d.options) ? ` [${(d.options as string[]).join(" / ")}]` : ""}`;
    case "operator.stop": return "operator stop: e-stop engaged";
    case "human.response": return `operator responds: ${d.action}`;
    case "run.end": return `run end: ${d.status}`;
    case "skill.postcondition_failed": case "skill.precondition_failed": return `${d.skill}: ${d.reason}`;
    case "adapter.call": return `${d.op}${d.to ? ` → ${d.to}` : ""}${d.max_speed ? ` @ ${d.max_speed} m/s` : ""}`;
    case "state.delta": {
      const delta = d.delta as Record<string, unknown> | undefined;
      const pose = delta?.robot_pose as { x: number; y: number } | undefined;
      return pose ? `pose (${pose.x.toFixed(2)}, ${pose.y.toFixed(2)})${delta?.battery_pct ? ` · battery ${Number(delta.battery_pct).toFixed(0)}%` : ""}` : "state updated";
    }
    case "perception.result": {
      if (d.cls !== undefined) {
        const found = (d.found as string[] | undefined) ?? [];
        return `looking for ${d.cls}: ${found.length ? `found ${found.join(", ")}` : "nothing found"}`;
      }
      const anomalies = (d.anomalies as string[] | undefined) ?? [];
      const checks = Object.keys((d.checks as Record<string, unknown> | undefined) ?? {});
      return `${d.target}: ${checks.length} check(s), ${anomalies.length ? `anomalies: ${anomalies.join(", ")}` : "all passed"}`;
    }
    case "robot.relocalized": return `tag ${d.marker_id} seen at ${d.at}: pose corrected by ${Number(d.correction_m).toFixed(2)} m, ${(Number(d.correction_rad) * 180 / Math.PI).toFixed(1)}°`;
    case "localization.missed": return `at ${d.at}: tag ${Array.isArray(d.markers) ? (d.markers as unknown[]).join(", ") : "?"} expected, not seen`;
    case "localization.rejected": return `at ${d.at}: tag ${d.marker_id} read ${Number(d.jump_m).toFixed(2)} m off, ignored`;
    case "sim.arrival": return `simulator: stopped ${Number(d.miss_m).toFixed(2)} m from the target, belief off by ${Number(d.error_m).toFixed(2)} m${d.blocked_by ? ` (${d.blocked_by})` : ""}`;
    case "safety.geofence": { const p = d.pose as { x: number; y: number } | undefined; return `outside the working area at (${p?.x.toFixed(2)}, ${p?.y.toFixed(2)})`; }
    case "safety.battery_low": return `battery ${d.battery_pct}% below ${d.min_pct}%`;
    case "safety.speed_capped": return `speed cap ${d.applied} m/s`;
    case "safety.armed": return "safety monitor armed";
    case "safety.disarmed": return `safety monitor disarmed after ${d.checks} checks`;
    case "skill.start": return `${d.skill}${Number(d.attempt) > 0 ? ` (attempt ${d.attempt})` : ""}`;
    case "step.end": return `step ${d.index}: ${d.skill} → ${d.outcome}`;
    default: {
      const rest = Object.fromEntries(Object.entries(d).filter(([k]) => !["ts", "run_id", "kind", "sim_t"].includes(k)));
      return Object.keys(rest).length ? JSON.stringify(rest) : "";
    }
  }
}
