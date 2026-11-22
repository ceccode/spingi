"""FakePerceiver: answers with configured objects.

Given the robot (anything with a `pose`, such as FakeAdapter), it sees only what the head camera would have in view
from where the robot stands, by the rule SimPerceiver applies (CameraView, ADR-0024). `spingi run --adapter fake`
builds it that way, so a plan's detections and inspections come out as they do on the simulator. Without a robot it
sees every configured object wherever it is, which is what the unit tests of retry and escalation want.
"""

from __future__ import annotations

import random
from typing import Protocol

from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose2D, Pose3D
from spingi.perception.view import HEAD_CAMERA, CameraView


class Posed(Protocol):
    pose: Pose2D


class FakePerceiver:
    def __init__(
        self,
        objects: list[ObjectRef] | None = None,
        always_fail: bool = False,
        false_negative_rate: float = 0.0,
        seed: int = 0,
        robot: Posed | None = None,
        view: CameraView = HEAD_CAMERA,
    ) -> None:
        self.objects = objects or []
        self.always_fail = always_fail
        self.false_negative_rate = false_negative_rate
        self.robot = robot
        self.view = view
        self._rng = random.Random(seed)
        self.detect_calls = 0

    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]:
        self.detect_calls += 1
        if self.always_fail or self._rng.random() < self.false_negative_rate:
            return []
        return [o for o in self.objects if o.cls == cls and self._in_view(o)]

    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None:
        for o in self.objects:
            if o.marker_id == marker_id and self._in_view(o):
                return o.pose
        return None

    def _in_view(self, obj: ObjectRef) -> bool:
        """Always, without a robot; with one, an object the scene does not place cannot be in view."""
        if self.robot is None:
            return True
        return obj.pose is not None and self.view.sees(self.robot.pose, obj.pose.x, obj.pose.y)
