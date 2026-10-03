/** Reading an episode (docs/episode-format.md). No dependency on rendering. */
import { strFromU8, unzipSync } from "fflate";
import YAML from "yaml";

export interface Pose2D { x: number; y: number; yaw: number }
export interface Manifest {
  format_version: string; run_id: string; created_at: string;
  robot: { model: string; adapter: string };
  plan_id: string; status: string; steps_completed: number; steps_total: number;
  duration_s: number; sim_time_s?: number | null; sample_rate_hz?: number | null; files: string[];
}
export interface Sample {
  t: number; robot: Pose2D & { mode: string }; battery_pct?: number;
  objects?: Record<string, { x: number; y: number; z: number }>;
}
export interface Event { ts: number; run_id: string; kind: string; sim_t?: number; [k: string]: unknown }
export interface Scene {
  robot?: { pose?: Pose2D; battery_pct?: number };
  locations?: Record<string, { pose: Pose2D; tolerance_m?: number }>;
  obstacles?: { x: number; y: number; w?: number; d?: number; h?: number }[];
  objects?: Record<string, { cls: string; pose?: { x: number; y: number; z: number }; size?: number[] }>;
}
export interface Plan { id: string; description?: string; steps: { skill: string; params: Record<string, unknown> }[] }
export interface Episode {
  manifest: Manifest; scene: Scene; plan: Plan; events: Event[]; trajectory: Sample[];
  frames: Map<string, Blob>; video?: Blob;
}

export const SUPPORTED_FORMAT = "0.1";

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
  if (manifest.format_version !== SUPPORTED_FORMAT) {
    throw new Error(`episode format ${manifest.format_version} not supported (expected ${SUPPORTED_FORMAT})`);
  }
  const frames = new Map<string, Blob>();
  let video: Blob | undefined;
  for (const [p, data] of Object.entries(files)) {
    if (p.startsWith("frames/")) frames.set(p.slice("frames/".length).replace(/\.png$/, ""), new Blob([data as BlobPart], { type: "image/png" }));
    if (p === "run.mp4") video = new Blob([data as BlobPart], { type: "video/mp4" });
  }
  return {
    manifest,
    scene: YAML.parse(text("scene.yaml")) as Scene,
    plan: YAML.parse(text("plan.yaml")) as Plan,
    events: parseJsonl<Event>(text("events.jsonl")),
    trajectory: files["trajectory.jsonl"] ? parseJsonl<Sample>(text("trajectory.jsonl")) : [],
    frames,
    video,
  };
}

export function loadEpisodeFromZip(buf: ArrayBuffer): Episode {
  return parseEpisodeFiles(unzipSync(new Uint8Array(buf)));
}

export function wrapAngle(a: number): number {
  return ((((a + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)) - Math.PI;
}

/** Pose linearly interpolated at time t; outside the range it holds the first or last sample. */
export function poseAt(trajectory: Sample[], t: number): Pose2D & { mode: string } {
  const first = trajectory[0];
  if (!first) return { x: 0, y: 0, yaw: 0, mode: "idle" };
  if (t <= first.t) return { ...first.robot };
  const last = trajectory[trajectory.length - 1]!;
  if (t >= last.t) return { ...last.robot };
  let lo = 0;
  let hi = trajectory.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (trajectory[mid]!.t <= t) lo = mid;
    else hi = mid;
  }
  const a = trajectory[lo]!;
  const b = trajectory[hi]!;
  const u = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
  return {
    x: a.robot.x + (b.robot.x - a.robot.x) * u,
    y: a.robot.y + (b.robot.y - a.robot.y) * u,
    yaw: a.robot.yaw + wrapAngle(b.robot.yaw - a.robot.yaw) * u,
    mode: u < 1 ? a.robot.mode : b.robot.mode,
  };
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
