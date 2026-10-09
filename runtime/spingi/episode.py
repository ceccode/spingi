"""Episode writing (docs/episode-format.md): the shareable artifact of a run.

The run folder IS the episode: `events.jsonl` and `frames/` are already there; here we add
`manifest.json`, `scene.yaml`, `plan.yaml` and `trajectory.jsonl`. The pydantic models are the
source of truth for the JSON schemas in `docs/schemas/` (test `tests/unit/test_episode.py`).
"""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from spingi.core.events import EventLog

FORMAT_VERSION = "0.1"


class RobotInfo(BaseModel):
    model: str = Field(description="robot profile name: unitree_g1, unitree_go2")
    adapter: str = Field(description="e.g. fake, sim_mujoco; the real robot's adapter later")


class RunConfig(BaseModel):
    """What is needed to run the episode again (spingi replay). Optional: older episodes do not have it."""

    perception_noise: float = Field(default=0.0, description="perceiver false-negative rate")
    position_sigma_m: float = Field(default=0.0, description="perceiver position noise, metres")
    seed: int = 0
    perception: str = Field(default="truth", description="truth (scene ground truth) or markers (AprilTags in frames)")


class Manifest(BaseModel):
    format_version: Literal["0.1"] = FORMAT_VERSION
    run_id: str
    created_at: str = Field(description="RFC 3339, UTC")
    robot: RobotInfo
    plan_id: str
    status: str = Field(description="success | aborted | invalid_plan | deadline | estop | error")
    steps_completed: int
    steps_total: int
    duration_s: float = Field(description="wall-clock time between run.start and run.end")
    sim_time_s: float | None = Field(default=None, description="total simulated time, if the adapter provides it")
    sample_rate_hz: float | None = Field(default=None, description="nominal sample rate of trajectory.jsonl")
    config: RunConfig | None = Field(default=None, description="run settings, for replay")
    files: list[str]


class RobotSample(BaseModel):
    x: float
    y: float
    yaw: float
    mode: str


class ObjectSample(BaseModel):
    x: float
    y: float
    z: float


class TrajectorySample(BaseModel):
    t: float = Field(description="simulated seconds since the start of the run; same axis as `sim_t` in the events")
    robot: RobotSample
    battery_pct: float | None = None
    objects: dict[str, ObjectSample] | None = Field(default=None, description="only when they change")
    joints: dict[str, float] | None = None


def write_episode(
    run_dir: Path,
    *,
    log: EventLog,
    plan_path: Path | str,
    scene_path: Path | str,
    plan_id: str,
    steps_total: int,
    status: str,
    steps_completed: int,
    adapter: Any,
    adapter_name: str,
    robot_model: str,
    config: RunConfig | None = None,
) -> Manifest:
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plan_path, run_dir / "plan.yaml")
    shutil.copyfile(scene_path, run_dir / "scene.yaml")

    samples = [TrajectorySample.model_validate(r) for r in getattr(adapter, "trajectory", [])]
    with (run_dir / "trajectory.jsonl").open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(s.model_dump_json(exclude_none=True) + "\n")

    if (run_dir / "events.jsonl").exists() is False and log.events:
        with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
            for e in log.events:
                fh.write(e.to_jsonl() + "\n")

    ts = [e.ts for e in log.events]
    files = sorted(p.name + ("/" if p.is_dir() else "") for p in run_dir.iterdir() if p.name != "manifest.json")
    sample_rate = None
    dt = getattr(adapter, "dt", None)
    every = getattr(adapter, "sample_every", None)
    if dt and every:
        sample_rate = round(1.0 / (dt * every), 3)
    manifest = Manifest(
        run_id=log.run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        robot=RobotInfo(model=robot_model, adapter=adapter_name),
        plan_id=plan_id,
        status=status,
        steps_completed=steps_completed,
        steps_total=steps_total,
        duration_s=round(max(ts) - min(ts), 3) if ts else 0.0,
        sim_time_s=round(float(getattr(adapter, "sim_time_s", 0.0)), 3) if hasattr(adapter, "sim_time_s") else None,
        sample_rate_hz=sample_rate,
        config=config,
        files=files,
    )
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def zip_episode(run_dir: Path) -> Path:
    out = run_dir.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(run_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(run_dir.parent))
    return out


def read_manifest(run_dir: Path) -> Manifest:
    return Manifest.model_validate(json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")))


def read_trajectory(run_dir: Path) -> list[TrajectorySample]:
    with (run_dir / "trajectory.jsonl").open(encoding="utf-8") as fh:
        return [TrajectorySample.model_validate_json(line) for line in fh if line.strip()]


def json_schemas() -> dict[str, dict]:
    """The schemas published in docs/schemas/, generated from the models."""
    return {
        "manifest.schema.json": Manifest.model_json_schema(),
        "trajectory-sample.schema.json": TrajectorySample.model_json_schema(),
    }
