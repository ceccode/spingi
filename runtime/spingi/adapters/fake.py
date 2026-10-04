"""FakeAdapter: in-memory, instantaneous robot for unit tests and development without hardware.

Simulates the few things that matter to the runtime: pose, battery, e-stop, speed clamp,
optional latency, optional watchdog. Records every call in `calls`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from spingi.core.ports import Arm, Frame, GripResult, JointState
from spingi.core.types import Pose2D, Pose3D


class EstopEngaged(RuntimeError):
    pass


class FakeAdapter:
    def __init__(
        self,
        start: Pose2D | None = None,
        battery_pct: float = 100.0,
        speed_cap: float = 1.0,
        walk_delay_s: float = 0.0,
        battery_drain_per_m: float = 0.5,
        watchdog_ms: int | None = None,
        clock=time.monotonic,
    ) -> None:
        self.pose = start or Pose2D(x=0.0, y=0.0, yaw=0.0)
        self.battery_pct = battery_pct
        self.speed_cap = speed_cap
        self.walk_delay_s = walk_delay_s
        self.battery_drain_per_m = battery_drain_per_m
        self.watchdog_ms = watchdog_ms
        self._clock = clock
        self._last_heartbeat = clock()
        self.estopped = False
        self.blocked_by: str | None = None  # 'watchdog' when the watchdog stopped the last move
        self.stop_called = 0
        self.last_applied_speed: float | None = None
        self.calls: list[tuple[str, dict]] = []
        self.mode: Literal["idle", "walking", "manipulating", "estop"] = "idle"
        self._frame_counter = 0
        self.gripper_closed = False
        self.grasp_fails = False
        self.sim_time_s = 0.0  # advances by distance/speed on each walk_to: time "as if" it were walking
        self.trajectory: list[dict] = []
        self._sample()

    # --- locomotion --------------------------------------------------------
    async def walk_to(self, pose: Pose2D, max_speed: float) -> None:
        self._record("walk_to", pose=pose.model_dump(), max_speed=max_speed)
        self._guard()
        self.last_applied_speed = min(max_speed, self.speed_cap)
        self.blocked_by = None
        self.mode = "walking"
        if self.walk_delay_s:
            await asyncio.sleep(self.walk_delay_s)
        if self._watchdog_expired():
            self.blocked_by = "watchdog"
            await self.stop()
            return
        distance = self.pose.distance_to(pose)
        self._sample()
        self.battery_pct = max(0.0, self.battery_pct - distance * self.battery_drain_per_m)
        self.sim_time_s += distance / self.last_applied_speed
        self.pose = pose
        self.mode = "idle"
        self._sample()

    async def stop(self) -> None:
        self._record("stop")
        self.stop_called += 1
        if self.mode != "estop":
            self.mode = "idle"

    async def get_pose(self) -> Pose2D:
        return self.pose

    # --- manipulation ------------------------------------------------------
    async def move_arm(self, arm: Arm, target: Pose3D, duration_s: float) -> None:
        self._record("move_arm", arm=arm, target=target.model_dump(), duration_s=duration_s)
        self._guard()
        self.mode = "manipulating"
        self.mode = "idle"

    async def gripper(self, arm: Arm, action: Literal["open", "close"]) -> GripResult:
        """Closing always grasps something (the fake cannot tell what); `grasp_fails` simulates an empty grasp."""
        self._record("gripper", arm=arm, action=action)
        self._guard()
        self.gripper_closed = action == "close" and not self.grasp_fails
        return GripResult(holding=self.gripper_closed)

    # --- sensors -----------------------------------------------------------
    async def get_camera(self, name: str = "head") -> Frame:
        self._frame_counter += 1
        return Frame(id=f"fake-{self._frame_counter}", ts=self._clock(), camera=name)

    async def get_joint_state(self) -> JointState:
        return JointState(names=[], positions=[])

    async def get_battery(self) -> float:
        return self.battery_pct

    # --- safety ------------------------------------------------------------
    async def estop(self) -> None:
        self._record("estop")
        self.estopped = True
        self.mode = "estop"

    async def heartbeat(self) -> None:
        self._last_heartbeat = self._clock()

    @property
    def clock(self):
        """The fake moves instantly, so its robot time is wall-clock time."""
        from spingi.core.clock import WallClock

        return WallClock()

    def set_speed_limit(self, max_speed: float) -> float:
        self.speed_cap = min(self.speed_cap, max_speed)
        return self.speed_cap

    def reset_estop(self) -> None:
        """Manual reset, like the physical button. Not exposed to the runtime."""
        self.estopped = False
        self.mode = "idle"

    # --- internals ---------------------------------------------------------
    def _record(self, name: str, **kw) -> None:
        self.calls.append((name, kw))

    def _sample(self) -> None:
        self.trajectory.append(
            {
                "t": round(self.sim_time_s, 3),
                "robot": {"x": self.pose.x, "y": self.pose.y, "yaw": self.pose.yaw, "mode": self.mode},
                "battery_pct": round(self.battery_pct, 2),
            }
        )

    def _guard(self) -> None:
        if self.estopped:
            raise EstopEngaged("e-stop engaged: no motion command accepted")

    def _watchdog_expired(self) -> bool:
        if self.watchdog_ms is None:
            return False
        return (self._clock() - self._last_heartbeat) * 1000 > self.watchdog_ms
