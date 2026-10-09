"""inspect(target, checks): take a head-camera frame at a known location and evaluate simple checks.

Checks are strings: `present:<cls>` or `absent:<cls>` are evaluated with the Perceiver; anything else
is recorded as not evaluated (a VLM-backed Perceiver will take them in v1). An inspection that finds
an anomaly is still a successful inspection: the anomalies are in the evidence, for the plan or the
operator to act on.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import StateDelta, WorldState


class InspectParams(BaseModel):
    target: str = Field(min_length=1, description="Known location the robot must be at")
    checks: list[str] = Field(default_factory=list, description="`present:<cls>`, `absent:<cls>` or free text")


class InspectSkill(Skill):
    name = "inspect"
    requires = frozenset({"camera"})
    Params = InspectParams
    default_deadline_s = 20.0

    def preconditions(self, params: InspectParams, state: WorldState) -> Check:
        if state.robot.mode == "estop":
            return Check.failed("robot in e-stop")
        target = state.location(params.target)
        if target is None:
            return Check.failed(f"unknown location: {params.target}")
        dist = state.robot.pose.distance_to(target.pose)
        if dist > target.tolerance_m:
            return Check.failed(f"{dist:.2f} m from {params.target}, tolerance {target.tolerance_m} m")
        return Check.passed()

    async def execute(self, params: InspectParams, ctx: SkillContext) -> SkillResult:
        frame = await ctx.robot.get_camera("head")
        results: dict[str, dict[str, Any]] = {}
        delta = StateDelta()
        for check in params.checks:
            kind, _, cls = check.partition(":")
            if kind in ("present", "absent") and cls:
                detections = await ctx.perceiver.detect(frame, cls)
                for obj in detections:
                    delta.objects_upsert[obj.id] = obj
                passed = bool(detections) if kind == "present" else not detections
                results[check] = {"passed": passed, "detections": len(detections)}
            else:
                results[check] = {"passed": None, "note": "not evaluated in v0"}
        anomalies = [c for c, r in results.items() if r["passed"] is False]
        ctx.log.emit(
            "perception.result",
            frame_id=frame.id,
            frame_ref=frame.data_ref,
            target=params.target,
            checks=results,
            anomalies=anomalies,
        )
        return SkillResult.success(
            delta if delta.objects_upsert else None,
            frame_id=frame.id,
            target=params.target,
            checks=results,
            anomalies=anomalies,
            passed=not anomalies,
        )
