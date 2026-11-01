"""Where the robot really is, from a landmark it sees (ADR-0016). Plane geometry: x, y and the heading.

The robot's pose estimate drifts. When the camera sees a landmark, the perceiver places it in the same drifted
frame as the estimate; the landmark's real place is on the map. The transform that moves the seen landmark onto its
real place is the drift, and applying it to the estimate gives the robot's real pose.
"""

from __future__ import annotations

import math

from spingi.core.types import Landmark, Pose2D, Pose3D

FACING_LIMIT_RAD = math.radians(75)  # a tag seen more obliquely than this is a thin sliver: not read


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def yaw_of(pose: Pose3D) -> float:
    """The rotation about the vertical in a pose's quaternion: for a marker, the direction its face looks."""
    return math.atan2(2 * (pose.qw * pose.qz + pose.qx * pose.qy), 1 - 2 * (pose.qy**2 + pose.qz**2))


def facing(x: float, y: float, z: float, yaw: float) -> Pose3D:
    """A marker pose: its centre, and a rotation about the vertical by the direction its face looks."""
    return Pose3D(x=x, y=y, z=z, qz=math.sin(yaw / 2), qw=math.cos(yaw / 2))


def pose_from_landmark(estimate: Pose2D, seen: Pose3D, landmark: Landmark) -> Pose2D:
    """The robot believes it is at `estimate` and sees the landmark at `seen`, in the same believed frame; the
    landmark is really at `landmark.pose`. Turning the believed frame about the seen landmark and moving it onto the
    real one moves the estimate onto the robot's real pose."""
    turn = wrap(landmark.pose.yaw - yaw_of(seen))
    c, s = math.cos(turn), math.sin(turn)
    rx, ry = estimate.x - seen.x, estimate.y - seen.y
    return Pose2D(
        x=landmark.pose.x + c * rx - s * ry,
        y=landmark.pose.y + s * rx + c * ry,
        yaw=wrap(estimate.yaw + turn),
    )


def in_view(pose: Pose2D, landmark: Landmark, max_range_m: float, half_fov_rad: float) -> bool:
    """Whether a camera looking along `pose` should see the landmark: near enough, inside the field of view, and
    with the tag's face turned towards the camera."""
    dx, dy = landmark.pose.x - pose.x, landmark.pose.y - pose.y
    dist = math.hypot(dx, dy)
    if dist > max_range_m or dist < 1e-6:
        return False
    if abs(wrap(math.atan2(dy, dx) - pose.yaw)) > half_fov_rad:
        return False
    return abs(wrap(math.atan2(-dy, -dx) - landmark.pose.yaw)) <= FACING_LIMIT_RAD
