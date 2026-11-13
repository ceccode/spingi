/** An episode in words: the rows of its card, a line for each event, the text over the 3D view. Plain strings
 *  only, whatever the episode holds: the page sets them as text. */
import type { Episode, Event, Pose2D, RobotPose } from "./episode";

/** The rows of the episode card, as plain strings. */
export function manifestRows(ep: Episode): [string, string][] {
  const m = ep.manifest;
  const seconds = Number(m.sim_time_s ?? m.duration_s);
  return [
    ["Outcome", `${m.status} · ${m.steps_completed}/${m.steps_total} steps`],
    ["Robot", `${m.robot?.model} · ${m.robot?.adapter}`],
    ["Duration", Number.isFinite(seconds) ? `${seconds.toFixed(1)} s simulated` : "unknown"],
    [
      "Created",
      String(m.created_at ?? "")
        .replace("T", " ")
        .replace("Z", " UTC"),
    ],
    ["Plan", String(ep.plan.description ?? ep.plan.id ?? "")],
  ];
}

/** The events the list sets apart: the safety monitor's and the operator's. */
export function eventTone(kind: string): "safety" | "human" | null {
  if (kind.startsWith("safety")) return "safety";
  if (kind.startsWith("human") || kind === "operator.stop") return "human";
  return null;
}

/** An event in a line of text. One whose fields are not what its kind promises is shown as it was written. */
export function describeEvent(ev: Event): string {
  try {
    return describeByKind(ev);
  } catch {
    return describeRaw(ev);
  }
}

function describeRaw(ev: Event): string {
  const rest = Object.fromEntries(Object.entries(ev).filter(([k]) => !["ts", "run_id", "kind", "sim_t"].includes(k)));
  return Object.keys(rest).length ? JSON.stringify(rest) : "";
}

/** A field that holds a list, or none; anything else is not what the kind promises. */
function listOf(v: unknown): unknown[] {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v)) throw new TypeError("not a list");
  return v;
}

function describeByKind(ev: Event): string {
  const d = ev as Record<string, unknown>;
  switch (ev.kind) {
    case "step.start":
      return `step ${d.index}: ${d.skill} ${JSON.stringify(d.params)}`;
    case "skill.end":
      return `${d.skill} → ${d.outcome}${d.reason ? ` (${d.reason})` : ""}`;
    case "say":
      return `"${d.text}"`;
    case "human.request":
      return `operator: ${d.skill} — ${d.reason}${Array.isArray(d.options) ? ` [${(d.options as string[]).join(" / ")}]` : ""}`;
    case "operator.stop":
      return "operator stop: e-stop engaged";
    case "human.response":
      return `operator responds: ${d.action}`;
    case "run.end":
      return `run end: ${d.status}`;
    case "skill.postcondition_failed":
    case "skill.precondition_failed":
      return `${d.skill}: ${d.reason}`;
    case "adapter.call":
      return `${d.op}${d.to ? ` → ${d.to}` : ""}${d.max_speed ? ` @ ${d.max_speed} m/s` : ""}`;
    case "state.delta": {
      const delta = d.delta as Record<string, unknown> | undefined;
      const pose = delta?.robot_pose as { x: number; y: number } | undefined;
      return pose
        ? `pose (${pose.x.toFixed(2)}, ${pose.y.toFixed(2)})${delta?.battery_pct ? ` · battery ${Number(delta.battery_pct).toFixed(0)}%` : ""}`
        : "state updated";
    }
    case "perception.result": {
      if (d.cls !== undefined) {
        const found = listOf(d.found);
        return `looking for ${d.cls}: ${found.length ? `found ${found.join(", ")}` : "nothing found"}`;
      }
      const anomalies = listOf(d.anomalies);
      const checks = Object.keys((d.checks as Record<string, unknown> | undefined) ?? {});
      return `${d.target}: ${checks.length} check(s), ${anomalies.length ? `anomalies: ${anomalies.join(", ")}` : "all passed"}`;
    }
    case "robot.relocalized":
      return `tag ${d.marker_id} seen at ${d.at}: pose corrected by ${Number(d.correction_m).toFixed(2)} m, ${((Number(d.correction_rad) * 180) / Math.PI).toFixed(1)}°`;
    case "localization.missed":
      return `at ${d.at}: tag ${Array.isArray(d.markers) ? (d.markers as unknown[]).join(", ") : "?"} expected, not seen`;
    case "localization.rejected":
      return `at ${d.at}: tag ${d.marker_id} read ${Number(d.jump_m).toFixed(2)} m off, ignored`;
    case "sim.arrival":
      return `simulator: stopped ${Number(d.miss_m).toFixed(2)} m from the target, belief off by ${Number(d.error_m).toFixed(2)} m${d.blocked_by ? ` (${d.blocked_by})` : ""}`;
    case "safety.geofence": {
      const p = d.pose as { x: number; y: number } | undefined;
      return `outside the working area at (${p?.x.toFixed(2)}, ${p?.y.toFixed(2)})`;
    }
    case "safety.battery_low":
      return `battery ${d.battery_pct}% below ${d.min_pct}%`;
    case "safety.speed_capped":
      return `speed cap ${d.applied} m/s`;
    case "safety.armed":
      return "safety monitor armed";
    case "safety.disarmed":
      return `safety monitor disarmed after ${d.checks} checks`;
    case "skill.start":
      return `${d.skill}${Number(d.attempt) > 0 ? ` (attempt ${d.attempt})` : ""}`;
    case "step.end":
      return `step ${d.index}: ${d.skill} → ${d.outcome}`;
    default:
      return describeRaw(ev);
  }
}

/** The text over the 3D view: the time, where the robot is, what it is doing and how far off its belief is. */
export function hudText(t: number, pose: RobotPose, estimate: Pose2D | null): string {
  const height = pose.z !== undefined ? `  z ${pose.z.toFixed(2)}` : "";
  const off = estimate
    ? `\nbelieves it is ${Math.hypot(estimate.x - pose.x, estimate.y - pose.y).toFixed(2)} m away (orange)`
    : "";
  return `t ${t.toFixed(2)} s\nx ${pose.x.toFixed(2)}  y ${pose.y.toFixed(2)}${height}  yaw ${pose.yaw.toFixed(2)}\n${pose.mode}${off}`;
}
