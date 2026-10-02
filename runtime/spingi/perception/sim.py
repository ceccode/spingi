"""SimPerceiver: ground truth from the MuJoCo scene, with optional noise.

Lets the runtime logic (retries, escalation, inspections) be tested without a vision model: an
object is "seen" when it is within range and inside the head camera's horizontal field of view.
"""

from __future__ import annotations

import math
import random

from spingi.adapters.sim_mujoco.scene import load_scene_yaml
from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose3D


class SimPerceiver:
    def __init__(
        self,
        adapter,
        max_range_m: float = 3.0,
        half_fov_rad: float = math.radians(40.0),
        false_negative_rate: float = 0.0,
        position_sigma_m: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.adapter = adapter
        self.max_range_m = max_range_m
        self.half_fov_rad = half_fov_rad
        self.false_negative_rate = false_negative_rate
        self.position_sigma_m = position_sigma_m
        self._rng = random.Random(seed)
        scene = load_scene_yaml(adapter.scene_path)
        self._classes = {oid: obj["cls"] for oid, obj in (scene.get("objects") or {}).items()}
        self._markers = {oid: obj.get("marker_id") for oid, obj in (scene.get("objects") or {}).items()}
        self.detect_calls = 0

    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]:
        self.detect_calls += 1
        found: list[ObjectRef] = []
        for oid, pos in self.adapter.object_positions().items():
            if self._classes.get(oid) != cls or not self._visible(pos):
                continue
            if self._rng.random() < self.false_negative_rate:
                continue
            found.append(
                ObjectRef(id=oid, cls=cls, pose=self._noisy(pos), confidence=0.95, marker_id=self._markers.get(oid))
            )
        return found

    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None:
        for oid, mid in self._markers.items():
            if mid == marker_id:
                pos = self.adapter.object_positions().get(oid)
                if pos is not None and self._visible(pos):
                    return self._noisy(pos)
        return None

    def _visible(self, pos: dict[str, float]) -> bool:
        robot = self.adapter.pose
        dx, dy = pos["x"] - robot.x, pos["y"] - robot.y
        dist = math.hypot(dx, dy)
        if dist > self.max_range_m:
            return False
        bearing = math.atan2(dy, dx) - robot.yaw
        bearing = (bearing + math.pi) % (2 * math.pi) - math.pi
        return abs(bearing) <= self.half_fov_rad

    def _noisy(self, pos: dict[str, float]) -> Pose3D:
        n = lambda v: v + (self._rng.gauss(0.0, self.position_sigma_m) if self.position_sigma_m else 0.0)  # noqa: E731
        return Pose3D(x=n(pos["x"]), y=n(pos["y"]), z=n(pos["z"]))
