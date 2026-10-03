"""wait_for_human(prompt, timeout_s): stop and wait until an operator confirms, e.g. "load the box, then continue".

The operator answers `continue` or `abort`. Continue succeeds. Abort, or no answer within the timeout, makes the
step fail; plans usually set `on_failure: {then: abort}` on this step so that a refusal ends the run.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from spingi.core.ports import HumanRequest
from spingi.core.skill import Skill, SkillContext, SkillResult
from spingi.core.types import StateDelta


class WaitForHumanParams(BaseModel):
    prompt: str = Field(min_length=1, max_length=300, description="What the operator should do before continuing")
    timeout_s: float = Field(default=300.0, gt=0, le=3600)


class WaitForHumanSkill(Skill):
    name = "wait_for_human"
    Params = WaitForHumanParams
    default_deadline_s = 3600.0  # the skill enforces its own timeout_s

    async def execute(self, params: WaitForHumanParams, ctx: SkillContext) -> SkillResult:
        if ctx.human is None:
            return SkillResult.needs_human("no operator gateway configured")
        await ctx.robot.stop()  # the robot stays still while a person works next to it
        request = HumanRequest(
            run_id=ctx.log.run_id, step_index=-1, skill=self.name, reason=params.prompt, options=["continue", "abort"]
        )
        ctx.log.emit("human.request", skill=self.name, reason=params.prompt, options=request.options)
        try:
            response = await asyncio.wait_for(ctx.human.ask(request, timeout_s=params.timeout_s), params.timeout_s)
        except TimeoutError:
            ctx.log.emit("human.timeout", skill=self.name)
            return SkillResult.recoverable(f"no confirmation within {params.timeout_s:g} s")
        ctx.log.emit("human.response", skill=self.name, action=response.action, note=response.note)
        if response.action != "continue":
            return SkillResult.recoverable(f"operator answered {response.action}")
        return SkillResult.success(StateDelta(robot_mode="idle"), confirmed=True)
