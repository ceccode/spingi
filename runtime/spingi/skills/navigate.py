"""navigate(to): brings the robot to a known location."""

from __future__ import annotations

from pydantic import BaseModel, Field

from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import StateDelta, WorldState

MIN_BATTERY_PCT = 10.0


class NavigateParams(BaseModel):
    to: str = Field(min_length=1, description="Name of a known location")
    max_speed: float = Field(default=0.5, gt=0.0, le=2.0, description="m/s; the runtime may reduce it")


class NavigateSkill(Skill):
    name = "navigate"
    Params = NavigateParams
    default_deadline_s = 60.0

    def preconditions(self, params: NavigateParams, state: WorldState) -> Check:
        if state.robot.mode == "estop":
            return Check.failed("robot in e-stop")
        if state.location(params.to) is None:
            return Check.failed(f"unknown location: {params.to}")
        if state.robot.battery_pct < MIN_BATTERY_PCT:
            return Check.failed(f"battery {state.robot.battery_pct:.0f}% below the minimum")
        return Check.passed()

    async def execute(self, params: NavigateParams, ctx: SkillContext) -> SkillResult:
        target = ctx.state.locations[params.to]
        ctx.log.emit("adapter.call", op="walk_to", to=params.to, max_speed=params.max_speed)
        await ctx.robot.walk_to(target.pose, max_speed=params.max_speed)
        pose = await ctx.robot.get_pose()
        battery = await ctx.robot.get_battery()
        delta = StateDelta(robot_pose=pose, robot_mode="idle", battery_pct=battery)
        return SkillResult.success(delta, reached=params.to, distance_m=round(pose.distance_to(target.pose), 3))

    def postconditions(self, params: NavigateParams, state: WorldState) -> Check:
        target = state.locations[params.to]
        dist = state.robot.pose.distance_to(target.pose)
        if dist > target.tolerance_m:
            return Check.failed(f"{dist:.2f} m from {params.to}, tolerance {target.tolerance_m} m")
        return Check.passed()

    async def abort(self) -> None:
        return None  # the Executor calls robot.stop(); the skill has no state to clean up
