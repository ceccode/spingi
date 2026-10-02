"""detect(cls, expect=1): look for objects of a class with the head camera and add them to the world state."""

from __future__ import annotations

from pydantic import BaseModel, Field

from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import StateDelta, WorldState


class DetectParams(BaseModel):
    cls: str = Field(min_length=1, description="Object class to look for, e.g. red_box")
    expect: int = Field(default=1, ge=1, description="Minimum number of objects that must be found")


class DetectSkill(Skill):
    name = "detect"
    Params = DetectParams
    default_deadline_s = 15.0

    def preconditions(self, params: DetectParams, state: WorldState) -> Check:
        if state.robot.mode != "idle":
            return Check.failed(f"robot must be idle to detect, is {state.robot.mode}")
        return Check.passed()

    async def execute(self, params: DetectParams, ctx: SkillContext) -> SkillResult:
        frame = await ctx.robot.get_camera("head")
        found = await ctx.perceiver.detect(frame, params.cls)
        ctx.log.emit(
            "perception.result",
            frame_id=frame.id,
            frame_ref=frame.data_ref,
            cls=params.cls,
            found=[o.id for o in found],
        )
        if len(found) < params.expect:
            return SkillResult.recoverable(
                f"found {len(found)} '{params.cls}', expected {params.expect}", frame_id=frame.id
            )
        delta = StateDelta(objects_upsert={o.id: o for o in found})
        return SkillResult.success(delta, objects=[o.model_dump() for o in found], frame_id=frame.id)
