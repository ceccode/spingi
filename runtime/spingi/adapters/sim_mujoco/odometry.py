"""What the robot believes about where it is (ADR-0016): its odometry, which drifts, and the corrections it gets.

A walking robot estimates its pose from its legs and its IMU. Both are slightly wrong: a stride read a little long or
short, a turn read a little large or small, a gyroscope with a bias, feet that slip more on gravel than on concrete.
Here the simulator knows the true motion of the base at every tick and hands the estimate a corrupted copy of it.

The estimate is kept as a planar transform from the true pose to the believed one (`offset`): the identity while
nothing has drifted or been corrected. Kinematic mode never drifts, but a relocalization still moves its frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from spingi.core.types import Pose2D

Planar = tuple[float, float, float]  # x, y, yaw
IDENTITY: Planar = (0.0, 0.0, 0.0)


def compose(a: Planar, b: Planar) -> Planar:
    """`b` expressed in `a`'s frame, then in the world: a ∘ b."""
    c, s = math.cos(a[2]), math.sin(a[2])
    return (a[0] + c * b[0] - s * b[1], a[1] + s * b[0] + c * b[1], _wrap(a[2] + b[2]))


def inverse(a: Planar) -> Planar:
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-c * a[0] - s * a[1], s * a[0] - c * a[1], _wrap(-a[2]))


def planar(p: Pose2D) -> Planar:
    return (p.x, p.y, p.yaw)


def as_pose(p: Planar) -> Pose2D:
    return Pose2D(x=p[0], y=p[1], yaw=p[2])


@dataclass(frozen=True)
class OdometryErrors:
    """How wrong the odometry is. Systematic errors are drawn once per run (the robot's calibration that day);
    random errors grow with the square root of the distance walked or the angle turned. Rough figures for a legged
    robot whose heading comes from an IMU calibrated at power-on: a few per cent of the distance, a per cent of every
    turn, a few tenths of a degree a minute."""

    stride_scale: float = 0.03  # per run, sigma: forward steps read 3 % long or short
    side_scale: float = 0.05  # per run, sigma: sideways steps are read worse
    turn_scale: float = 0.01  # per run, sigma: turns read 1 % large or small (the IMU's gyroscope)
    gyro_bias: float = 0.0001  # per run, sigma, rad/s: about 20 degrees an hour, standing still too
    walk_noise: float = 0.02  # m per sqrt(m) walked
    heading_noise: float = 0.01  # rad per sqrt(m) walked and per sqrt(rad) turned
    # Loose ground: the feet slip and the legs' account of the motion is worse.
    slip: dict[str, float] = field(default_factory=lambda: {"gravel": 2.0, "dirt": 2.0, "grass": 1.5})


class Odometry:
    """The robot's belief about its pose, as an offset from the truth: believed = offset ∘ true."""

    def __init__(self, errors: OdometryErrors | None, rng: np.random.Generator) -> None:
        self.errors = errors  # None: the odometry follows the true motion exactly
        self.rng = rng
        self.offset: Planar = IDENTITY
        if errors is not None:
            self.scale = (
                rng.normal(0.0, errors.stride_scale),
                rng.normal(0.0, errors.side_scale),
                rng.normal(0.0, errors.turn_scale),
            )
            self.bias = rng.normal(0.0, errors.gyro_bias)

    def estimate(self, true: Planar) -> Planar:
        return true if self.offset == IDENTITY else compose(self.offset, true)  # exact while nothing drifted

    def relocalize(self, true: Planar, believed: Planar) -> None:
        """From now on the robot believes it is at `believed`: the offset that maps the truth onto it."""
        self.offset = compose(believed, inverse(true))

    def update(self, before: Planar, after: Planar, dt: float, surface: str | None = None) -> None:
        """The base moved from `before` to `after` (true poses) in `dt`: the estimate moves by a corrupted copy of
        that motion, measured in the body frame as the legs and the IMU measure it."""
        if self.errors is None:
            return
        e = self.errors
        dx, dy, dyaw = compose(inverse(before), after)
        dist = math.hypot(dx, dy)
        slip = e.slip.get(surface or "", 1.0)
        n = self.rng.standard_normal(3)
        walk = e.walk_noise * slip * math.sqrt(dist)
        heading = e.heading_noise * math.sqrt(slip * dist + abs(dyaw))
        measured = (
            dx * (1 + self.scale[0]) + walk * n[0],
            dy * (1 + self.scale[1]) + walk * n[1],
            dyaw * (1 + self.scale[2]) + self.bias * dt + heading * n[2],
        )
        believed = compose(self.estimate(before), measured)
        self.offset = compose(believed, inverse(after))


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
