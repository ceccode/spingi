"""say(text): feedback to people. Always SUCCESS; the text ends up in the event log."""

from __future__ import annotations

from pydantic import BaseModel, Field

from spingi.core.skill import Skill, SkillContext, SkillResult


class SayParams(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class SaySkill(Skill):
    name = "say"
    Params = SayParams
    default_deadline_s = 5.0

    async def execute(self, params: SayParams, ctx: SkillContext) -> SkillResult:
        ctx.log.emit("say", text=params.text)
        return SkillResult.success(None, said=params.text)
