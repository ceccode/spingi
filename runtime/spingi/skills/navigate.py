"""navigate(to, via=[]): brings the robot to a known location, optionally through intermediate waypoints.

There is no path planner in v0 (ADR-0004: plans are data): the plan author lists the waypoints that keep the
robot clear of shelving and walls. Each leg is a straight line; if a leg is interrupted (obstacle, safety stop)
the skill returns where it got to and the postcondition fails. At an intermediate waypoint the robot faces the next
one instead of turning to the waypoint's own heading: only the destination's heading matters, and every turn on the
spot costs time and, on a walking robot, drift.

The robot's pose is its own estimate, and it drifts as the robot walks (ADR-0016). Where the scene has landmarks,
the robot looks for one before it leaves and at every waypoint it reaches: a tag seen at a known place tells it
where it really is. If the correction puts it away from the waypoint, it walks the difference and looks again.

A retried navigate resumes the route from the waypoint the robot stands at, instead of walking back to the first one.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from spingi.core.localization import in_view, pose_from_landmark, wrap
from spingi.core.skill import Check, Skill, SkillContext, SkillResult
from spingi.core.types import Pose2D, StateDelta, WorldState

MIN_BATTERY_PCT = 10.0
# A tag is used up to 10 times its side away (30 cm: 3 m), about 48 pixels on the Go2's camera. Farther, its position
# is still good but the direction its face looks is off by a few degrees, which turns the robot and, through the
# turn, moves it by as much again (measured in MuJoCo with the noisy camera: ADR-0016).
TAG_READ_RANGE = 10.0
LANDMARK_HALF_FOV_RAD = math.radians(45)  # where the head camera looks; the perceiver decides what it really sees
MAX_FIXES = 2  # per waypoint: look, correct, walk the difference, look again
FIX_FRAMES = 3  # frames per look, averaged: one noisy frame turns the robot by a degree or two
MAX_JUMP_M = 2.0  # a larger correction is a misread tag, not drift: refused
MAX_TURN_RAD = 0.6
FINAL_YAW_TOL_RAD = 0.15  # at the destination the heading matters: the camera looks where the inspection is


class NavigateParams(BaseModel):
    to: str = Field(min_length=1, description="Name of a known location")
    via: list[str] = Field(default_factory=list, description="Intermediate known locations, in order")
    max_speed: float = Field(default=0.5, gt=0.0, le=2.0, description="m/s; the runtime may reduce it")


class NavigateSkill(Skill):
    name = "navigate"
    requires = frozenset({"locomotion"})
    Params = NavigateParams
    default_deadline_s = 60.0

    def preconditions(self, params: NavigateParams, state: WorldState) -> Check:
        if state.robot.mode == "estop":
            return Check.failed("robot in e-stop")
        for name in [*params.via, params.to]:
            if state.location(name) is None:
                return Check.failed(f"unknown location: {name}")
        if state.robot.battery_pct < MIN_BATTERY_PCT:
            return Check.failed(f"battery {state.robot.battery_pct:.0f}% below the minimum")
        return Check.passed()

    async def execute(self, params: NavigateParams, ctx: SkillContext) -> SkillResult:
        legs = [*params.via, params.to]
        reached: list[str] = []
        here = await ctx.robot.get_pose()
        here = await self._fix(ctx, _where(ctx.state, here), here) or here  # put down by hand: a few degrees off
        for k in range(len(params.via) - 1, -1, -1):  # standing at a waypoint of the route: go on from there
            via = ctx.state.locations[params.via[k]]
            if here.distance_to(via.pose) <= via.tolerance_m:
                legs = legs[k + 1 :]
                break
        for i, name in enumerate(legs):
            waypoint = ctx.state.locations[name]
            target = waypoint.pose
            final = i == len(legs) - 1
            if not final:
                after = ctx.state.locations[legs[i + 1]].pose
                target = Pose2D(x=target.x, y=target.y, yaw=math.atan2(after.y - target.y, after.x - target.x))
            if not await self._reach(ctx, name, target, waypoint.tolerance_m, params.max_speed, final):
                break  # interrupted leg: report where we are, let the postcondition decide
            reached.append(name)
        pose = await ctx.robot.get_pose()
        battery = await ctx.robot.get_battery()
        target = ctx.state.locations[params.to]
        delta = StateDelta(robot_pose=pose, robot_mode="idle", battery_pct=battery)
        return SkillResult.success(
            delta, reached=params.to, path=reached, distance_m=round(pose.distance_to(target.pose), 3)
        )

    async def _reach(
        self, ctx: SkillContext, name: str, target: Pose2D, tolerance: float, max_speed: float, final: bool
    ) -> bool:
        """Walks to one waypoint and, where a landmark is in view, corrects the pose and walks the difference, up to
        MAX_FIXES times; the walk after the last correction is the final try. False when the walk was interrupted
        away from the waypoint."""
        for attempt in range(MAX_FIXES):
            pose = await self._walk(ctx, name, target, max_speed)
            if pose.distance_to(target) > tolerance:
                if attempt == 0:
                    return False  # the leg was interrupted: an obstacle, a safety stop
                continue  # a short walk back after a correction stalled: a walking robot does, now and then
            if final and not _facing(pose, target):
                continue  # the last turn did not finish (a walking robot can stall turning on the spot): again
            fixed = await self._fix(ctx, name, pose)
            if fixed is None or _arrived(fixed, target, tolerance, final):
                return True
        pose = await self._walk(ctx, name, target, max_speed)
        return pose.distance_to(target) <= tolerance

    async def _walk(self, ctx: SkillContext, name: str, target: Pose2D, max_speed: float) -> Pose2D:
        """One walk towards the waypoint; where the robot believes it ended up."""
        ctx.log.emit("adapter.call", op="walk_to", to=name, max_speed=max_speed)
        await ctx.robot.walk_to(target, max_speed=max_speed)
        return await ctx.robot.get_pose()

    async def _fix(self, ctx: SkillContext, at: str, pose: Pose2D) -> Pose2D | None:
        """Looks for the landmarks that should be in view from the estimated pose; the first one seen gives the
        robot its real pose. None when there is nothing to look for or nothing was seen."""
        if "camera" not in ctx.robot.capabilities:
            return None
        expected = sorted(
            (lm for lm in ctx.state.landmarks if in_view(pose, lm, TAG_READ_RANGE * lm.size_m, LANDMARK_HALF_FOV_RAD)),
            key=lambda lm: math.hypot(lm.pose.x - pose.x, lm.pose.y - pose.y),
        )
        if not expected:
            return None
        sightings: dict[int, list[Pose2D]] = {lm.marker_id: [] for lm in expected}
        frames = []
        for _ in range(FIX_FRAMES):
            frame = await ctx.robot.get_camera("head")
            frames.append(frame)
            for lm in expected:
                seen = await ctx.perceiver.localize(frame, lm.marker_id)
                if seen is not None:
                    sightings[lm.marker_id].append(pose_from_landmark(pose, seen, lm))
        frame = frames[0]
        for lm in expected:
            if 2 * len(sightings[lm.marker_id]) <= FIX_FRAMES:
                continue  # seen in a minority of the frames: not a reliable reading
            fixed = _mean(sightings[lm.marker_id])
            jump, turn = pose.distance_to(fixed), abs(wrap(fixed.yaw - pose.yaw))
            if jump > MAX_JUMP_M or turn > MAX_TURN_RAD:
                ctx.log.emit(
                    "localization.rejected",
                    at=at,
                    marker_id=lm.marker_id,
                    frame_id=frame.id,
                    jump_m=round(jump, 3),
                    turn_rad=round(turn, 3),
                )
                continue
            await ctx.robot.relocalize(fixed)
            ctx.log.emit(
                "robot.relocalized",
                at=at,
                marker_id=lm.marker_id,
                frame_id=frame.id,
                frame_ref=frame.data_ref,
                correction_m=round(jump, 3),
                correction_rad=round(turn, 3),
                pose={"x": round(fixed.x, 3), "y": round(fixed.y, 3), "yaw": round(fixed.yaw, 3)},
            )
            return fixed
        ctx.log.emit(
            "localization.missed",
            at=at,
            frame_id=frame.id,
            frame_ref=frame.data_ref,
            markers=[lm.marker_id for lm in expected],
        )
        return None

    def postconditions(self, params: NavigateParams, state: WorldState) -> Check:
        target = state.locations[params.to]
        dist = state.robot.pose.distance_to(target.pose)
        if dist > target.tolerance_m:
            return Check.failed(f"{dist:.2f} m from {params.to}, tolerance {target.tolerance_m} m")
        return Check.passed()

    async def abort(self) -> None:
        return None  # the Executor calls robot.stop(); the skill has no state to clean up


def _facing(pose: Pose2D, target: Pose2D) -> bool:
    return abs(wrap(pose.yaw - target.yaw)) <= FINAL_YAW_TOL_RAD


def _arrived(pose: Pose2D, target: Pose2D, tolerance: float, final: bool) -> bool:
    """At a waypoint on the way being within its tolerance is enough; at the destination the inspection that follows
    needs the robot well inside it and facing the right way."""
    if final:
        return pose.distance_to(target) <= tolerance / 2 and _facing(pose, target)
    return pose.distance_to(target) <= tolerance


def _mean(poses: list[Pose2D]) -> Pose2D:
    n = len(poses)
    yaw = math.atan2(sum(math.sin(p.yaw) for p in poses) / n, sum(math.cos(p.yaw) for p in poses) / n)
    return Pose2D(x=sum(p.x for p in poses) / n, y=sum(p.y for p in poses) / n, yaw=yaw)


def _where(state: WorldState, pose: Pose2D) -> str:
    """The known location the robot stands at, for the log; "here" when it is at none."""
    for name, loc in state.locations.items():
        if pose.distance_to(loc.pose) <= loc.tolerance_m:
            return name
    return "here"
