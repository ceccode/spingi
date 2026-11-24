"""Declarative safety limits. Loaded from the `safety:` section of a scene file."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from spingi.core.types import Pose2D

# A misspelled limit (`max_sped:`) must not leave the robot without that limit: unknown keys are errors (ADR-0028).
STRICT = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Geofence(BaseModel):
    """Axis-aligned working area. Anything outside it stops the robot."""

    model_config = STRICT

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @model_validator(mode="after")
    def _ordered(self) -> Geofence:
        if not (self.x_min < self.x_max and self.y_min < self.y_max):
            raise ValueError("x_min must be less than x_max and y_min less than y_max: this fence contains nothing")
        return self

    def contains(self, pose: Pose2D) -> bool:
        return self.x_min <= pose.x <= self.x_max and self.y_min <= pose.y <= self.y_max


class SafetyLimits(BaseModel):
    model_config = STRICT

    geofence: Geofence | None = None
    max_speed: float | None = Field(default=None, gt=0, description="m/s; applied as a cap on the adapter")
    min_battery_pct: float = Field(default=5.0, ge=0, le=100)
    estop_on_geofence: bool = Field(
        default=True, description="leaving the fence e-stops the robot (latched, needs a reset); false: a plain stop"
    )
