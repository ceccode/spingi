"""Declarative safety limits. Loaded from the `safety:` section of a scene file."""

from __future__ import annotations

from pydantic import BaseModel, Field

from spingi.core.types import Pose2D


class Geofence(BaseModel):
    """Axis-aligned working area. Anything outside it stops the robot."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def contains(self, pose: Pose2D) -> bool:
        return self.x_min <= pose.x <= self.x_max and self.y_min <= pose.y <= self.y_max


class SafetyLimits(BaseModel):
    geofence: Geofence | None = None
    max_speed: float | None = Field(default=None, gt=0, description="m/s; applied as a cap on the adapter")
    min_battery_pct: float = Field(default=5.0, ge=0, le=100)
    estop_on_geofence: bool = Field(default=False, description="e-stop instead of a plain stop when leaving the fence")
