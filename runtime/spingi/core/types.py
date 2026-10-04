"""Shared domain types. Everything is a BaseModel: validation and JSON for free (ADR-0002)."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RobotMode = Literal["idle", "walking", "manipulating", "estop"]

# Names of locations, objects and classes travel into XML (the simulator), prompts (the planner), file-like ids and
# terminals: one conservative alphabet for all of them.
NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class Strict(BaseModel):
    """No NaN or infinity anywhere in robot geometry: a NaN setpoint must be rejected, not sent to a motor."""

    model_config = ConfigDict(allow_inf_nan=False)


class Pose2D(Strict):
    x: float
    y: float
    yaw: float = 0.0  # rad

    def distance_to(self, other: Pose2D) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


class Pose3D(Strict):
    x: float
    y: float
    z: float
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0


class Location(Strict):
    name: str = Field(pattern=NAME_PATTERN)
    pose: Pose2D
    tolerance_m: float = Field(default=0.15, gt=0)


class ObjectRef(Strict):
    id: str = Field(pattern=NAME_PATTERN)
    cls: str = Field(pattern=NAME_PATTERN)
    pose: Pose3D | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    marker_id: int | None = None


class RobotState(Strict):
    pose: Pose2D
    battery_pct: float = 100.0
    holding: ObjectRef | None = None
    mode: RobotMode = "idle"


class WorldState(BaseModel):
    """Symbolic state of the world. Updated only by the Executor through StateDelta."""

    locations: dict[str, Location] = Field(default_factory=dict)
    objects: dict[str, ObjectRef] = Field(default_factory=dict)
    robot: RobotState
    ts: float = 0.0

    def location(self, name: str) -> Location | None:
        return self.locations.get(name)


class StateDelta(Strict):
    """State change proposed by a skill. Skills do not touch WorldState directly."""

    robot_pose: Pose2D | None = None
    robot_mode: RobotMode | None = None
    battery_pct: float | None = None
    holding: ObjectRef | None = None
    clear_holding: bool = False
    objects_upsert: dict[str, ObjectRef] = Field(default_factory=dict)
    objects_remove: list[str] = Field(default_factory=list)
    ts: float | None = None


def apply_delta(state: WorldState, delta: StateDelta | None) -> WorldState:
    """Returns a new WorldState with the delta applied. Pure: does not modify the input."""
    if delta is None:
        return state
    new = state.model_copy(deep=True)
    if delta.robot_pose is not None:
        new.robot.pose = delta.robot_pose
    if delta.robot_mode is not None:
        new.robot.mode = delta.robot_mode
    if delta.battery_pct is not None:
        new.robot.battery_pct = delta.battery_pct
    if delta.clear_holding:
        new.robot.holding = None
    elif delta.holding is not None:
        new.robot.holding = delta.holding
    for key, obj in delta.objects_upsert.items():
        new.objects[key] = obj
    for key in delta.objects_remove:
        new.objects.pop(key, None)
    if delta.ts is not None:
        new.ts = delta.ts
    return new
