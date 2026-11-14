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
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from spingi.core.events import EventLog
from spingi.core.ports import Recording

FORMAT_VERSION = "0.1"


class RobotInfo(BaseModel):
    model: str = Field(description="robot profile name: unitree_g1, unitree_go2")
    adapter: str = Field(description="e.g. fake, sim_mujoco; the real robot's adapter later")


PerceptionKind = Literal["truth", "markers"]
LocomotionKind = Literal["kinematic", "physics"]
OdometryKind = Literal["truth", "drift"]


class RunConfig(BaseModel):
    """What is needed to run the episode again (spingi replay). Optional: older episodes do not have it. Its fields
    have the names and values of `SessionConfig`'s: an episode with a value the runtime does not know is refused."""

    perception_noise: float = Field(default=0.0, description="perceiver false-negative rate")
    position_sigma_m: float = Field(default=0.0, description="perceiver position noise, metres")
    seed: int = 0
    perception: PerceptionKind = Field(
        default="truth", description="truth (scene ground truth) or markers (AprilTags in frames)"
    )
    locomotion: LocomotionKind = Field(
        default="kinematic", description="kinematic (the base glides) or physics (the robot walks)"
    )
    odometry: OdometryKind = Field(
        default="truth", description="truth (exact pose) or drift (the estimate drifts, ADR-0016)"
    )
    camera_noise: bool = Field(default=False, description="blurred, noisy, JPEG-compressed head-camera frames")


class CameraVideo(BaseModel):
    """A camera recorded continuously (`spingi run --camera-video`): frame i was taken at `start_t + i / fps`."""

    file: str = Field(description="the video, relative to the episode folder: head.mp4 (H.264)")
    camera: str = Field(description="the camera's name in the robot model: head")
    fps: float = Field(description="frames per second of simulated time")
    start_t: float = Field(description="simulated time of the first frame, on the axis of `t` and `sim_t`")
    frames: int
    width: int
    height: int


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
    camera_video: CameraVideo | None = Field(default=None, description="the head camera, recorded continuously")
    files: list[str]


class RobotSample(BaseModel):
    x: float
    y: float
    yaw: float
    mode: str
    z: float | None = Field(default=None, description="base height, when it leaves the flat floor (terrain, physics)")
    roll: float | None = None
    pitch: float | None = None


class EstimateSample(BaseModel):
    x: float
    y: float
    yaw: float


class ObjectSample(BaseModel):
    x: float
    y: float
    z: float


class TrajectorySample(BaseModel):
    t: float = Field(description="simulated seconds since the start of the run; same axis as `sim_t` in the events")
    robot: RobotSample
    estimate: EstimateSample | None = Field(
        default=None, description="where the robot believes it is, when that differs from `robot` (ADR-0016)"
    )
    battery_pct: float | None = None
    objects: dict[str, ObjectSample] | None = Field(default=None, description="only when they change")
    joints: dict[str, float] | None = Field(default=None, description="joint angles in radians (physics locomotion)")


class RunOutcome(NamedTuple):
    """How the run of a plan ended."""

    plan_id: str
    status: str
    steps_completed: int
    steps_total: int


def write_episode(
    run_dir: Path,
    *,
    log: EventLog,
    plan_path: Path | str,
    scene_path: Path | str,
    outcome: RunOutcome,
    robot: RobotInfo,
    recording: Recording,
    config: RunConfig | None = None,
) -> Manifest:
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plan_path, run_dir / "plan.yaml")
    shutil.copyfile(scene_path, run_dir / "scene.yaml")

    if recording.terrain is not None:
        terrain = json.dumps(recording.terrain, separators=(",", ":"))
        (run_dir / "terrain.json").write_text(terrain + "\n", encoding="utf-8")

    samples = [TrajectorySample.model_validate(r) for r in recording.trajectory]
    with (run_dir / "trajectory.jsonl").open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(s.model_dump_json(exclude_none=True) + "\n")

    if not (run_dir / "events.jsonl").exists() and log.events:
        with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
            for e in log.events:
                fh.write(e.to_jsonl() + "\n")

    ts = [e.ts for e in log.events]
    files = sorted(p.name + ("/" if p.is_dir() else "") for p in run_dir.iterdir() if p.name != "manifest.json")
    camera = recording.camera_video
    manifest = Manifest(
        run_id=log.run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        robot=robot,
        plan_id=outcome.plan_id,
        status=outcome.status,
        steps_completed=outcome.steps_completed,
        steps_total=outcome.steps_total,
        duration_s=round(max(ts) - min(ts), 3) if ts else 0.0,
        sim_time_s=round(recording.sim_time_s, 3) if recording.sim_time_s is not None else None,
        sample_rate_hz=recording.sample_rate_hz,
        config=config,
        camera_video=CameraVideo.model_validate(camera) if camera else None,
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
