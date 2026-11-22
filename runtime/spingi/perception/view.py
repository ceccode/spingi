"""What the head camera has in view, for the perceivers that answer from the scene's truth (ADR-0024).

SimPerceiver and FakePerceiver both know where every object is. This rule decides which of them the camera would have
in its picture: near enough, and inside the horizontal field of view around the robot's heading. Plane geometry only:
height, occlusion and light play no part, so an object behind a shelf is still seen (`--perception markers` reads the
rendered frames when that matters).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from spingi.core.localization import wrap
from spingi.core.types import Pose2D


@dataclass(frozen=True)
class CameraView:
    """How far the camera reads an object, and how far either side of the robot's heading it looks."""

    max_range_m: float = 3.0
    half_fov_rad: float = math.radians(40.0)

    def sees(self, robot: Pose2D, x: float, y: float, max_range_m: float | None = None) -> bool:
        """Whether a robot standing at `robot` has the point (x, y) in view. `max_range_m` replaces the range for
        things read from farther than an object, such as a landmark's tag."""
        dx, dy = x - robot.x, y - robot.y
        if math.hypot(dx, dy) > (self.max_range_m if max_range_m is None else max_range_m):
            return False
        return abs(wrap(math.atan2(dy, dx) - robot.yaw)) <= self.half_fov_rad


HEAD_CAMERA = CameraView()
