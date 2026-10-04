"""Export Spingi episodes to a LeRobotDataset v3.0 folder (ADR-0009).

What goes in, per frame at a fixed rate (default 10 Hz, resampled from trajectory.jsonl):
  observation.state  float32[4]  base x, base y, base yaw, gripper closed (0/1)
  action             float32[4]  the same quantities at the next frame (the commanded target)
  timestamp, frame_index, episode_index, index, task_index, next.done, next.success

What does not go in yet: camera images. Spingi frames are sparse (one per inspection or detection), while
LeRobot expects a video at the dataset rate; images arrive when an adapter records the head camera at a fixed
rate. Joint states arrive with real locomotion.

Layout (from lerobot's own metadata code and published v3.0 datasets):
  meta/info.json · meta/stats.json · meta/tasks.parquet · meta/episodes/chunk-000/file-000.parquet
  data/chunk-000/file-000.parquet
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from spingi.core.events import EventLog
from spingi.episode import read_manifest, read_trajectory

CODEBASE_VERSION = "v3.0"
STATE_NAMES = ["base_x", "base_y", "base_yaw", "gripper_closed"]
MAX_EPISODE_S = 24 * 3600  # an episode longer than a day is not a run but a malformed file


def export_lerobot(episode_dirs: list[Path], out: Path, fps: int = 10) -> dict:
    """Writes the dataset and returns its info.json content."""
    import pandas as pd  # optional dependency: the `export` extra

    if not episode_dirs:
        raise ValueError("no episodes to export")
    not_episodes = [str(p) for p in episode_dirs if not (Path(p) / "manifest.json").is_file()]
    if not_episodes:
        raise ValueError(f"not episode folders (no manifest.json): {', '.join(not_episodes)}")
    out.mkdir(parents=True, exist_ok=True)

    tasks: dict[str, int] = {}
    frames: list[dict] = []
    episodes: list[dict] = []
    global_index = 0
    for ep_index, ep_dir in enumerate(episode_dirs):
        manifest = read_manifest(ep_dir)
        task = _task_text(ep_dir, manifest.plan_id)
        task_index = tasks.setdefault(task, len(tasks))
        states = _resample(ep_dir, fps)
        success = manifest.status == "success"
        start = global_index
        for i, (t, state) in enumerate(states):
            nxt = states[i + 1][1] if i + 1 < len(states) else state
            last = i == len(states) - 1
            frames.append(
                {
                    "observation.state": [float(v) for v in state],
                    "action": [float(v) for v in nxt],
                    "timestamp": float(t),
                    "frame_index": i,
                    "episode_index": ep_index,
                    "index": global_index,
                    "task_index": task_index,
                    "next.done": last,
                    "next.success": last and success,
                }
            )
            global_index += 1
        episodes.append(
            {
                "episode_index": ep_index,
                "tasks": [task],
                "length": len(states),
                "data/chunk_index": 0,
                "data/file_index": 0,
                "dataset_from_index": start,
                "dataset_to_index": global_index,
                "meta/episodes/chunk_index": 0,
                "meta/episodes/file_index": 0,
                "spingi/run_id": manifest.run_id,
                "spingi/status": manifest.status,
            }
        )

    df = pd.DataFrame(frames)
    df["timestamp"] = df["timestamp"].astype("float32")
    df["observation.state"] = df["observation.state"].map(lambda v: [float(x) for x in v])
    _write_parquet(df, out / "data" / "chunk-000" / "file-000.parquet", float32_lists=["observation.state", "action"])
    _write_parquet(pd.DataFrame(episodes), out / "meta" / "episodes" / "chunk-000" / "file-000.parquet")
    tasks_df = pd.DataFrame({"task_index": list(tasks.values())}, index=pd.Index(list(tasks.keys()), name="task"))
    (out / "meta").mkdir(parents=True, exist_ok=True)
    tasks_df.to_parquet(out / "meta" / "tasks.parquet")

    (out / "meta" / "stats.json").write_text(json.dumps(_stats(frames), indent=2) + "\n", encoding="utf-8")
    info = _info(fps, total_episodes=len(episodes), total_frames=global_index, total_tasks=len(tasks))
    (out / "meta" / "info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def _task_text(ep_dir: Path, fallback: str) -> str:
    import yaml

    try:
        plan = yaml.safe_load((ep_dir / "plan.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{ep_dir}: unreadable plan.yaml ({exc})") from exc
    if not isinstance(plan, dict):
        raise ValueError(f"{ep_dir}: plan.yaml is not a mapping")
    return str(plan.get("description") or plan.get("id") or fallback)


def _gripper_timeline(ep_dir: Path) -> list[tuple[float, float]]:
    """(sim_t, gripper state) changes, from successful pick and place skills in the event log."""
    changes: list[tuple[float, float]] = [(0.0, 0.0)]
    events = EventLog.read(ep_dir / "events.jsonl")
    for e in events:
        if e.kind == "skill.end" and e.data.get("outcome") == "success" and e.data.get("skill") in ("pick", "place"):
            t = float(e.data.get("sim_t", 0.0))
            changes.append((t, 1.0 if e.data["skill"] == "pick" else 0.0))
    return changes


def _resample(ep_dir: Path, fps: int) -> list[tuple[float, list[float]]]:
    samples = read_trajectory(ep_dir)
    if not samples:
        return []
    gripper = _gripper_timeline(ep_dir)
    end = samples[-1].t
    if not math.isfinite(end) or end < 0 or end > MAX_EPISODE_S:
        raise ValueError(f"{ep_dir}: trajectory ends at t={end}, outside 0..{MAX_EPISODE_S} s")
    n = math.floor(end * fps + 1e-9) + 1
    out: list[tuple[float, list[float]]] = []
    j = 0
    for k in range(n):
        t = k / fps
        while j + 1 < len(samples) and samples[j + 1].t <= t:
            j += 1
        a = samples[j]
        b = samples[j + 1] if j + 1 < len(samples) else a
        u = 0.0 if b.t == a.t else min(1.0, max(0.0, (t - a.t) / (b.t - a.t)))
        dyaw = (b.robot.yaw - a.robot.yaw + math.pi) % (2 * math.pi) - math.pi
        g = [v for (tc, v) in gripper if tc <= t][-1]
        out.append(
            (
                round(t, 6),
                [
                    a.robot.x + (b.robot.x - a.robot.x) * u,
                    a.robot.y + (b.robot.y - a.robot.y) * u,
                    (a.robot.yaw + dyaw * u + math.pi) % (2 * math.pi) - math.pi,
                    g,
                ],
            )
        )
    return out


def _write_parquet(df, path: Path, float32_lists: list[str] | None = None) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    for name in float32_lists or []:
        idx = table.schema.get_field_index(name)
        table = table.set_column(idx, name, pa.array(df[name].tolist(), type=pa.list_(pa.float32())))
    pq.write_table(table, path)


def _stats(frames: list[dict]) -> dict:
    def vec_stats(key: str) -> dict:
        cols = list(zip(*(f[key] for f in frames), strict=True))
        mean = [sum(c) / len(c) for c in cols]
        std = [math.sqrt(sum((x - m) ** 2 for x in c) / len(c)) for c, m in zip(cols, mean, strict=True)]
        return {
            "min": [min(c) for c in cols],
            "max": [max(c) for c in cols],
            "mean": mean,
            "std": std,
            "count": [len(frames)],
        }

    def scalar_stats(key: str) -> dict:
        vals = [float(f[key]) for f in frames]
        m = sum(vals) / len(vals)
        return {
            "min": [min(vals)],
            "max": [max(vals)],
            "mean": [m],
            "std": [math.sqrt(sum((x - m) ** 2 for x in vals) / len(vals))],
            "count": [len(vals)],
        }

    out = {k: vec_stats(k) for k in ("observation.state", "action")}
    out.update({k: scalar_stats(k) for k in ("timestamp", "frame_index", "episode_index", "index", "task_index")})
    return out


def _info(fps: int, *, total_episodes: int, total_frames: int, total_tasks: int) -> dict:
    def scalar(dtype: str) -> dict:
        return {"dtype": dtype, "shape": [1], "names": None, "fps": float(fps)}

    vec = {"dtype": "float32", "shape": [len(STATE_NAMES)], "names": {"axes": STATE_NAMES}, "fps": float(fps)}
    return {
        "codebase_version": CODEBASE_VERSION,
        "robot_type": "unitree_g1",
        "total_episodes": total_episodes,
        "total_frames": total_frames,
        "total_tasks": total_tasks,
        "chunks_size": 1000,
        "fps": fps,
        "splits": {"train": f"0:{total_episodes}"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": None,
        "features": {
            "observation.state": dict(vec),
            "action": dict(vec),
            "timestamp": scalar("float32"),
            "frame_index": scalar("int64"),
            "episode_index": scalar("int64"),
            "index": scalar("int64"),
            "task_index": scalar("int64"),
            "next.done": scalar("bool"),
            "next.success": scalar("bool"),
        },
        "data_files_size_in_mb": 100,
        "video_files_size_in_mb": 500,
    }
