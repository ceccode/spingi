/** Reading an episode (docs/episode-format.md) and checking it: episodes come from strangers too. What the replay
 *  cannot do without is refused when the episode is loaded, with a message that names it (the archive's size, the
 *  format's version, a line of the trajectory or of the event log, the head camera's video). What is only drawn
 *  around the robot (a place, an obstacle, an object, a landmark of the scene) is skipped when it is malformed.
 *  No dependency on rendering. */
import { strFromU8, unzipSync } from "fflate";
import YAML from "yaml";
import { parseTerrain, type Terrain } from "./terrain";

export interface Pose2D { x: number; y: number; yaw: number }

export interface Manifest {
  format_version: string; run_id: string; created_at: string;
  robot: { model: string; adapter: string };
  plan_id: string; status: string; steps_completed: number; steps_total: number;
  duration_s: number; sim_time_s?: number | null; sample_rate_hz?: number | null; files: string[];
  camera_video?: unknown; // checked by `cameraVideoOf` before it is played
}

/** Base height and tilt: present when the robot left the flat floor (outdoor ground, physics walking). */
export interface BaseLift { z?: number; roll?: number; pitch?: number }

/** Where the robot really was, and what it was doing. */
export type RobotPose = Pose2D & BaseLift & { mode: string };

export interface Position { x: number; y: number; z: number }

export interface Sample {
  t: number; robot: RobotPose; battery_pct?: number;
  estimate?: Pose2D; // where the robot believed it was, when its odometry had drifted (ADR-0016)
  objects?: Record<string, Position>;
  joints?: Record<string, number>; // joint angles in radians (physics walking)
}

export interface Event { ts: number; run_id: string; kind: string; sim_t?: number; [k: string]: unknown }

/** scene.yaml as the format describes it. An episode from a stranger may hold anything here: what is drawn is read
 *  through `locationsOf`, `obstaclesOf`, `objectsOf` and `landmarksOf`, which skip a malformed entry. */
export interface Scene {
  robot?: { pose?: Pose2D; battery_pct?: number };
  locations?: Record<string, { pose: Pose2D; tolerance_m?: number }>;
  obstacles?: { x: number; y: number; w?: number; d?: number; h?: number; color?: string }[];
  objects?: Record<string, { cls: string; pose?: { x: number; y: number; z: number }; size?: number[] }>;
  landmarks?: unknown;
}

/** A named place: where the robot stands there, and how far from it still counts as there (0: not said). */
export interface Place { name: string; x: number; y: number; yaw: number; tolerance: number }

/** A box in the robot's way. `index` is its place in the scene's list, the order of the terrain's `obstaclesZ`;
 *  `color` is a #rrggbb colour, or null for the default one. */
export interface Obstacle { index: number; x: number; y: number; w: number; d: number; h: number; color: string | null }

/** Something the robot can pick up: its centre and its size along x, y and z. */
export interface SceneObject { id: string; x: number; y: number; z: number; size: [number, number, number] }

/** A tag at a known place (scene.yaml `landmarks`): x, y on the ground, the direction its face looks, its height. */
export interface Landmark { markerId: number; x: number; y: number; facing: number; height: number; size: number; post: boolean }

export interface Plan { id: string; description?: string; steps: { skill: string; params: Record<string, unknown> }[] }

/** The head camera recorded continuously (`spingi run --camera-video`): frame i was taken at start + i / fps. */
export interface CameraVideo { blob: Blob; camera: string; fps: number; start: number; frames: number; width: number; height: number }

export interface Episode {
  manifest: Manifest; scene: Scene; plan: Plan; events: Event[]; trajectory: Sample[];
  frames: Map<string, Blob>;
  terrain: Terrain | null; // outdoor ground; null on a flat floor
  cameraVideo: CameraVideo | null;
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

export const MAX_SCENE_ENTRIES = 1024; // places, obstacles and objects drawn, each

/** Episodes come from strangers too: refuse archives that would exhaust the browser's memory (zip bombs). */
export const MAX_ZIP_BYTES = 200 * 1024 * 1024;

export const MAX_UNZIPPED_BYTES = 500 * 1024 * 1024;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The mapping a YAML file holds, or an empty one when it holds something else. */
const mapping = (v: unknown): Record<string, unknown> => (isRecord(v) ? v : {});

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
    scene: mapping(YAML.parse(text("scene.yaml"))) as Scene,
    plan: mapping(YAML.parse(text("plan.yaml"))) as unknown as Plan,
    events: checkEvents(parseJsonl<Event>(text("events.jsonl"))),
    trajectory: files["trajectory.jsonl"] ? checkTrajectory(parseJsonl<Sample>(text("trajectory.jsonl"))) : [],
    frames,
    terrain: files["terrain.json"] ? parseTerrain(JSON.parse(text("terrain.json"))) : null,
    cameraVideo: cameraVideoOf(manifest, files),
  };
}

/** The manifest's `camera_video`, checked: a malformed entry refuses the episode; a video left out of the archive
 *  (episodes kept for replay drop it) leaves the replay without it. */
export function cameraVideoOf(manifest: Manifest, files: Record<string, Uint8Array>): CameraVideo | null {
  const raw = manifest.camera_video;
  if (raw === undefined || raw === null) return null;
  const v = (typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const count = (x: unknown, max: number): x is number => Number.isInteger(x) && (x as number) >= 1 && (x as number) <= max;
  const { file, camera, fps, start_t: start, frames, width, height } = v;
  const ok = typeof file === "string" && /^[\w-]+\.mp4$/.test(file) && typeof camera === "string" && camera.length <= 64
    && typeof fps === "number" && fps > 0 && fps <= 120 && typeof start === "number" && Number.isFinite(start) && start >= 0
    && count(frames, 10_000_000) && count(width, 8192) && count(height, 8192);
  if (!ok) throw new Error("malformed camera_video in manifest.json");
  const data = files[file as string];
  if (!data) return null;
  return {
    blob: new Blob([data as BlobPart], { type: "video/mp4" }), camera: camera as string, fps: fps as number,
    start: start as number, frames: frames as number, width: width as number, height: height as number,
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
    if (ok && s.objects !== undefined) {
      ok = isRecord(s.objects) && Object.values(s.objects).every((p) => isRecord(p) && [p.x, p.y, p.z].every(isNumber));
    }
    if (!ok) throw new Error(`malformed trajectory line ${i + 1}`);
  });
  return samples;
}

/** A line of the event log that is not an event is refused at load time, like a malformed trajectory line. */
export function checkEvents(events: Event[]): Event[] {
  events.forEach((e, i) => {
    const ok = isRecord(e) && typeof e.kind === "string" && isNumber(e.ts) && (typeof e.sim_t !== "number" || isNumber(e.sim_t));
    if (!ok) throw new Error(`malformed events line ${i + 1}`);
  });
  return events;
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

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** The entries of one of the scene's mappings, as many as are drawn; none when it is not a mapping. */
function sceneEntries(v: unknown): [string, unknown][] {
  return isRecord(v) ? Object.entries(v).slice(0, MAX_SCENE_ENTRIES) : [];
}

/** The scene's named places, checked: a malformed entry is skipped, never drawn half-way. */
export function locationsOf(scene: Scene): Place[] {
  const out: Place[] = [];
  for (const [name, raw] of sceneEntries(scene.locations)) {
    if (!isRecord(raw) || !isRecord(raw.pose)) continue;
    const { x, y, yaw } = raw.pose;
    const tolerance = raw.tolerance_m ?? 0;
    if (isNumber(x) && isNumber(y) && isNumber(yaw) && isNumber(tolerance)) out.push({ name, x, y, yaw, tolerance });
  }
  return out;
}

/** The scene's obstacles, checked like its places. One left out leaves the others at their place in the list. */
export function obstaclesOf(scene: Scene): Obstacle[] {
  if (!Array.isArray(scene.obstacles)) return [];
  const out: Obstacle[] = [];
  const isSize = (v: unknown): v is number => isNumber(v) && v >= 0;
  scene.obstacles.slice(0, MAX_SCENE_ENTRIES).forEach((raw: unknown, index) => {
    if (!isRecord(raw)) return;
    const { x, y, w = 0.5, d = 0.5, h = 1 } = raw;
    if (!isNumber(x) || !isNumber(y) || !isSize(w) || !isSize(d) || !isSize(h)) return;
    out.push({ index, x, y, w, d, h, color: typeof raw.color === "string" && HEX_COLOR.test(raw.color) ? raw.color : null });
  });
  return out;
}

/** The scene's objects, checked like its places. Without a pose an object rests on the floor at the origin. */
export function objectsOf(scene: Scene): SceneObject[] {
  const out: SceneObject[] = [];
  const isSize = (v: unknown): v is number => isNumber(v) && v >= 0;
  for (const [id, raw] of sceneEntries(scene.objects)) {
    if (!isRecord(raw)) continue;
    const [sx, sy, sz] = raw.size === undefined ? [0.15, 0.1, 0.1] : Array.isArray(raw.size) ? (raw.size as unknown[]) : [];
    if (!isSize(sx) || !isSize(sy) || !isSize(sz)) continue;
    const pose = raw.pose ?? { x: 0, y: 0, z: sz / 2 };
    if (!isRecord(pose)) continue;
    const { x, y, z } = pose;
    if (isNumber(x) && isNumber(y) && isNumber(z)) out.push({ id, x, y, z, size: [sx, sy, sz] });
  }
  return out;
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
