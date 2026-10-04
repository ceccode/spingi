"""Scenes as data: an initial WorldState loaded from YAML (ADR-0004)."""

from __future__ import annotations

from pathlib import Path

import yaml

from spingi.core.types import Location, ObjectRef, Pose2D, Pose3D, RobotState, WorldState
from spingi.safety.limits import SafetyLimits


def load_world(path: Path | str) -> WorldState:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    locations = {
        name: Location(name=name, pose=Pose2D(**loc["pose"]), tolerance_m=loc.get("tolerance_m", 0.15))
        for name, loc in (raw.get("locations") or {}).items()
    }
    objects = {}
    for oid, obj in (raw.get("objects") or {}).items():
        pose = obj.get("pose")
        objects[oid] = ObjectRef(
            id=oid,
            cls=obj["cls"],
            marker_id=obj.get("marker_id"),
            pose=Pose3D(**pose) if pose else None,
            confidence=obj.get("confidence", 1.0),
        )
    robot = raw.get("robot") or {}
    return WorldState(
        locations=locations,
        objects=objects,
        robot=RobotState(
            pose=Pose2D(**robot.get("pose", {"x": 0, "y": 0, "yaw": 0})), battery_pct=robot.get("battery_pct", 100.0)
        ),
    )


def load_safety_limits(path: Path | str) -> SafetyLimits:
    """The optional `safety:` section of a scene file; defaults when absent."""
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return SafetyLimits.model_validate(raw.get("safety") or {})


def load_routes(path: Path | str) -> dict[str, list[str]]:
    """The optional `routes:` section: "from->to" -> waypoints, given to the planner as a hint."""
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {str(k): [str(w) for w in v] for k, v in (raw.get("routes") or {}).items()}
