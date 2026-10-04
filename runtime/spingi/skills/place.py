"""place(at): put the held object down at the current location."""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import ObjectRef, Pose3D, StateDelta, WorldState


class PlaceParams(BaseModel):
    at: str = Field(min_length=1, description="Known location the robot must be at")
    arm: str = Field(default="right", pattern="^(left|right)$")
    height_m: float = Field(default=0.9, ge=0.0, le=1.5, description="Drop height used when the adapter cannot tell")


class PlaceSkill(Skill):
    name = "place"
    Params = PlaceParams
    default_deadline_s = 30.0

    def preconditions(self, params: PlaceParams, state: WorldState) -> Check:
        if state.robot.holding is None:
            return Check.failed("not holding anything")
        target = state.location(params.at)
        if target is None:
            return Check.failed(f"unknown location: {params.at}")
        dist = state.robot.pose.distance_to(target.pose)
        if dist > target.tolerance_m:
            return Check.failed(f"{dist:.2f} m from {params.at}, tolerance {target.tolerance_m} m")
        return Check.passed()

    async def execute(self, params: PlaceParams, ctx: SkillContext) -> SkillResult:
        held = ctx.state.robot.holding
        if held is None:  # the precondition guarantees it; never rely on assert, which `python -O` removes
            return SkillResult.needs_human("place called with an empty hand")
        pose = ctx.state.robot.pose
        drop = Pose3D(x=pose.x + 0.35 * math.cos(pose.yaw), y=pose.y + 0.35 * math.sin(pose.yaw), z=params.height_m)
        ctx.log.emit("adapter.call", op="move_arm", arm=params.arm, target=drop.model_dump())
        await ctx.robot.move_arm(params.arm, drop, duration_s=2.0)
        grip = await ctx.robot.gripper(params.arm, "open")
        if grip.holding:
            return SkillResult.recoverable(f"{held.id} did not leave the {params.arm} hand")
        if grip.released_at is not None:  # the adapter knows where it landed (simulation); else assume the drop pose
            drop = grip.released_at
        placed = ObjectRef(id=held.id, cls=held.cls, pose=drop, confidence=1.0, marker_id=held.marker_id)
        delta = StateDelta(clear_holding=True, objects_upsert={held.id: placed})
        return SkillResult.success(delta, object_id=held.id, at=params.at, pose=drop.model_dump())

    def postconditions(self, params: PlaceParams, state: WorldState) -> Check:
        if state.robot.holding is not None:
            return Check.failed(f"still holding {state.robot.holding.id}")
        return Check.passed()
