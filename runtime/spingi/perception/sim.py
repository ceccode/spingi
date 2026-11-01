"""SimPerceiver: ground truth from the MuJoCo scene, with optional noise.

Lets the runtime logic (retries, escalation, inspections) be tested without a vision model: an
object is "seen" when it is within range and inside the head camera's horizontal field of view.
What is seen is reported where the robot believes it is, in its estimated frame (ADR-0016), as a perceiver placing
it from the camera's believed pose would: if the robot's estimate has drifted, so has everything it sees. Landmarks
(tags at known places) are seen the same way, and only from the front.
"""

from __future__ import annotations

import math
import random

from spingi.adapters.sim_mujoco.scene import load_scene_yaml
from spingi.core.localization import FACING_LIMIT_RAD, facing, wrap
from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose3D
from spingi.scenes import parse_landmarks


class SimPerceiver:
    def __init__(
        self,
        adapter,
        max_range_m: float = 3.0,
        half_fov_rad: float = math.radians(40.0),
        false_negative_rate: float = 0.0,
        position_sigma_m: float = 0.0,
        seed: int = 0,
        landmark_range_m: float = 4.0,  # a tag is bigger than most objects and made to be read from afar
    ) -> None:
        self.adapter = adapter
        self.max_range_m = max_range_m
        self.half_fov_rad = half_fov_rad
        self.landmark_range_m = landmark_range_m
        self.false_negative_rate = false_negative_rate
        self.position_sigma_m = position_sigma_m
        self._rng = random.Random(seed)
        scene = load_scene_yaml(adapter.scene_path)
        self._classes = {oid: obj["cls"] for oid, obj in (scene.get("objects") or {}).items()}
        self._markers = {oid: obj.get("marker_id") for oid, obj in (scene.get("objects") or {}).items()}
        self._landmarks = {lm.marker_id: lm for lm in parse_landmarks(scene)}
        self.detect_calls = 0

    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]:
        self.detect_calls += 1
        found: list[ObjectRef] = []
        for oid, pos in self.adapter.object_positions().items():
            if self._classes.get(oid) != cls or not self._visible(pos["x"], pos["y"]):
                continue
            if self._rng.random() < self.false_negative_rate:
                continue
            found.append(
                ObjectRef(id=oid, cls=cls, pose=self._seen(pos), confidence=0.95, marker_id=self._markers.get(oid))
            )
        return found

    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None:
        for oid, mid in self._markers.items():
            if mid == marker_id:
                pos = self.adapter.object_positions().get(oid)
                if pos is not None and self._visible(pos["x"], pos["y"]):
                    return self._seen(pos)
        lm = self._landmarks.get(marker_id)
        if lm is None or not self._visible(lm.pose.x, lm.pose.y, self.landmark_range_m):
            return None
        robot = self.adapter.pose
        if abs(wrap(math.atan2(robot.y - lm.pose.y, robot.x - lm.pose.x) - lm.pose.yaw)) > FACING_LIMIT_RAD:
            return None  # seen from behind or edge on
        z = self.adapter.scene.ground_height(lm.pose.x, lm.pose.y) + lm.height_m
        x, y, yaw = self.adapter.believed(lm.pose.x, lm.pose.y, lm.pose.yaw)
        return facing(self._n(x), self._n(y), self._n(z), self._n(yaw))  # the heading noise in rad as in m

    def _visible(self, x: float, y: float, max_range_m: float | None = None) -> bool:
        robot = self.adapter.pose  # what the camera really sees depends on where the robot really is
        dx, dy = x - robot.x, y - robot.y
        dist = math.hypot(dx, dy)
        if dist > (max_range_m or self.max_range_m):
            return False
        bearing = math.atan2(dy, dx) - robot.yaw
        bearing = (bearing + math.pi) % (2 * math.pi) - math.pi
        return abs(bearing) <= self.half_fov_rad

    def _seen(self, pos: dict[str, float]) -> Pose3D:
        x, y, _ = self.adapter.believed(pos["x"], pos["y"])
        return Pose3D(x=self._n(x), y=self._n(y), z=self._n(pos["z"]))

    def _n(self, v: float) -> float:
        return v + (self._rng.gauss(0.0, self.position_sigma_m) if self.position_sigma_m else 0.0)
