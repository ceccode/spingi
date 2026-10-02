"""pick(object_id): grasp a known object within arm reach (ADR-0007: predefined grasp on standard containers)."""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import StateDelta, WorldState

REACH_M = 0.9  # base-to-object distance within which the predefined grasp works


class PickParams(BaseModel):
    object_id: str = Field(min_length=1, description="Id of an object with a known pose in the world state")
    arm: str = Field(default="right", pattern="^(left|right)$")


class PickSkill(Skill):
    name = "pick"
    Params = PickParams
    default_deadline_s = 30.0

    def preconditions(self, params: PickParams, state: WorldState) -> Check:
        if state.robot.holding is not None:
            return Check.failed(f"already holding {state.robot.holding.id}")
        obj = state.objects.get(params.object_id)
        if obj is None or obj.pose is None:
            return Check.failed(f"object {params.object_id} has no known pose")
        dist = math.hypot(obj.pose.x - state.robot.pose.x, obj.pose.y - state.robot.pose.y)
        if dist > REACH_M:
            return Check.failed(f"{params.object_id} is {dist:.2f} m away, reach is {REACH_M} m")
        return Check.passed()

    async def execute(self, params: PickParams, ctx: SkillContext) -> SkillResult:
        obj = ctx.state.objects[params.object_id]
        ctx.log.emit("adapter.call", op="move_arm", arm=params.arm, target=obj.pose.model_dump())
        await ctx.robot.move_arm(params.arm, obj.pose, duration_s=2.0)
        await ctx.robot.gripper(params.arm, "close")
        held = getattr(ctx.robot, "held_object", params.object_id)  # adapters that simulate grasping tell us
        if held != params.object_id:
            await ctx.robot.gripper(params.arm, "open")
            return SkillResult.recoverable(f"grasp failed: holding {held!r} instead of {params.object_id}")
        delta = StateDelta(holding=obj, objects_remove=[params.object_id])
        return SkillResult.success(delta, object_id=params.object_id)

    def postconditions(self, params: PickParams, state: WorldState) -> Check:
        if state.robot.holding is None or state.robot.holding.id != params.object_id:
            return Check.failed(f"not holding {params.object_id}")
        return Check.passed()
