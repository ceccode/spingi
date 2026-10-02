"""FakePerceiver: answers with configured objects. Used to test retry and escalation."""

from __future__ import annotations

import random

from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose3D


class FakePerceiver:
    def __init__(
        self,
        objects: list[ObjectRef] | None = None,
        always_fail: bool = False,
        false_negative_rate: float = 0.0,
        seed: int = 0,
    ) -> None:
        self.objects = objects or []
        self.always_fail = always_fail
        self.false_negative_rate = false_negative_rate
        self._rng = random.Random(seed)
        self.detect_calls = 0

    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]:
        self.detect_calls += 1
        if self.always_fail or self._rng.random() < self.false_negative_rate:
            return []
        return [o for o in self.objects if o.cls == cls]

    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None:
        for o in self.objects:
            if o.marker_id == marker_id:
                return o.pose
        return None
