"""`spingi replay`: run a recorded episode again and compare what happens (runtime-spec 8.1, golden episodes).

The plan, the scene and the run settings come from the episode itself. The comparison is on the behaviour, not
on timestamps: the sequence of steps and skill outcomes, operator requests, safety events, the final status, and
the robot's final position. A difference means the runtime, a skill or the simulator changed behaviour.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from spingi.core.events import Event, EventLog
from spingi.core.human import ScriptedHuman
from spingi.episode import read_manifest, read_trajectory
from spingi.robots import DEFAULT_ROBOT, get_profile
from spingi.session import SessionConfig, run_session

SIGNATURE_KINDS = ("step.start", "skill.end", "step.retry", "human.request", "human.response", "run.end")
POSITION_TOLERANCE_M = 0.05


class ReplayReport(BaseModel):
    episode: str
    same: bool
    recorded_status: str
    replayed_status: str
    differences: list[str] = Field(default_factory=list)


def signature(events: list[Event]) -> list[tuple[Any, ...]]:
    """The behaviour of a run, without timestamps or noise-free details."""
    sig: list[tuple[Any, ...]] = []
    for e in events:
        d = e.data
        if e.kind == "step.start":
            sig.append((e.kind, d.get("index"), d.get("skill")))
        elif e.kind == "skill.end":
            sig.append((e.kind, d.get("skill"), d.get("outcome")))
        elif e.kind == "step.retry":
            sig.append((e.kind, d.get("index")))
        elif e.kind in ("human.request", "human.response"):
            sig.append((e.kind, d.get("skill"), d.get("action")))
        elif e.kind == "run.end":
            sig.append((e.kind, d.get("status"), d.get("steps_completed")))
        elif e.kind.startswith("safety.") and e.kind not in ("safety.armed", "safety.disarmed", "safety.speed_capped"):
            sig.append((e.kind,))
    return sig


def _operator_answers(events: list[Event]) -> list[str]:
    return [e.data["action"] for e in events if e.kind == "human.response" and "action" in e.data]


async def replay(episode_dir: Path, runs_dir: Path | None = None) -> ReplayReport:
    manifest = read_manifest(episode_dir)
    recorded = EventLog.read(episode_dir / "events.jsonl")
    cfg_rec = manifest.config
    adapter = "sim" if manifest.robot.adapter == "sim_mujoco" else "fake"
    try:
        robot = get_profile(manifest.robot.model).alias
    except KeyError:
        robot = DEFAULT_ROBOT  # episodes recorded before robot profiles existed say "fake": they ran as the G1
    with tempfile.TemporaryDirectory() as tmp:
        cfg = SessionConfig(
            plan=episode_dir / "plan.yaml",
            scene=episode_dir / "scene.yaml",
            adapter=adapter,
            robot=robot,
            runs_dir=runs_dir or Path(tmp),
            perception_noise=cfg_rec.perception_noise if cfg_rec else 0.0,
            position_sigma_m=cfg_rec.position_sigma_m if cfg_rec else 0.0,
            seed=cfg_rec.seed if cfg_rec else 0,
        )
        # The operator gives the same answers, in the same order, as in the recording.
        human = ScriptedHuman(responses=_operator_answers(recorded), default="abort")
        result = await run_session(cfg, human)
        replayed_traj = read_trajectory(result.run_dir)

    diffs: list[str] = []
    a, b = signature(recorded), signature(result.log.events)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            diffs.append(f"event {i}: recorded {x} · replayed {y}")
            if len(diffs) >= 10:
                diffs.append("…")
                break
    recorded_traj = read_trajectory(episode_dir) if (episode_dir / "trajectory.jsonl").exists() else []
    if recorded_traj and replayed_traj:
        p, q = recorded_traj[-1].robot, replayed_traj[-1].robot
        dist = math.hypot(p.x - q.x, p.y - q.y)
        if dist > POSITION_TOLERANCE_M:
            diffs.append(f"final position differs by {dist:.2f} m")
    return ReplayReport(
        episode=str(episode_dir),
        same=not diffs,
        recorded_status=manifest.status,
        replayed_status=result.status,
        differences=diffs,
    )
