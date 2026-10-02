"""place(at): put the held object down at the current location."""

from __future__ import annotations

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
        assert held is not None  # guaranteed by the precondition
        pose = ctx.state.robot.pose
        import math

        drop = Pose3D(x=pose.x + 0.35 * math.cos(pose.yaw), y=pose.y + 0.35 * math.sin(pose.yaw), z=params.height_m)
        ctx.log.emit("adapter.call", op="move_arm", arm=params.arm, target=drop.model_dump())
        await ctx.robot.move_arm(params.arm, drop, duration_s=2.0)
        await ctx.robot.gripper(params.arm, "open")
        positions = getattr(ctx.robot, "object_positions", None)
        if callable(positions) and held.id in positions():
            p = positions()[held.id]
            drop = Pose3D(x=p["x"], y=p["y"], z=p["z"])
        placed = ObjectRef(id=held.id, cls=held.cls, pose=drop, confidence=1.0, marker_id=held.marker_id)
        delta = StateDelta(clear_holding=True, objects_upsert={held.id: placed})
        return SkillResult.success(delta, object_id=held.id, at=params.at, pose=drop.model_dump())

    def postconditions(self, params: PlaceParams, state: WorldState) -> Check:
        if state.robot.holding is not None:
            return Check.failed(f"still holding {state.robot.holding.id}")
        return Check.passed()
