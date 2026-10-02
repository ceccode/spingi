"""SafetyMonitor: observes the robot independently of the Executor and stops it when a limit is crossed.

It never plans and never recovers: it stops, emits an event and leaves the rest to the Executor
(postconditions fail, the step is retried or escalated). It also feeds the adapter watchdog.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from spingi.core.events import EventLog
from spingi.core.ports import RobotAdapter
from spingi.safety.limits import SafetyLimits


class SafetyMonitor:
    def __init__(
        self,
        robot: RobotAdapter,
        log: EventLog,
        limits: SafetyLimits,
        period_s: float = 0.05,
        clock=time.monotonic,
    ) -> None:
        self.robot = robot
        self.log = log
        self.limits = limits
        self.period_s = period_s  # 0 = check at every cooperative yield (fast simulation)
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self.violations: list[dict[str, Any]] = []
        self.checks = 0

    @property
    def tripped(self) -> bool:
        return bool(self.violations)

    async def start(self) -> None:
        if self.limits.max_speed is not None and hasattr(self.robot, "speed_cap"):
            previous = self.robot.speed_cap
            self.robot.speed_cap = min(previous, self.limits.max_speed)
            self.log.emit("safety.speed_capped", requested=previous, applied=self.robot.speed_cap)
        self.log.emit("safety.armed", limits=self.limits.model_dump(exclude_none=True), period_s=self.period_s)
        self._task = asyncio.create_task(self._loop(), name="safety-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.log.emit("safety.disarmed", checks=self.checks, violations=len(self.violations))

    async def check_once(self) -> None:
        """One pass of the checks. Public so tests can drive it without the loop."""
        self.checks += 1
        await self.robot.heartbeat()
        pose = await self.robot.get_pose()
        fence = self.limits.geofence
        if fence is not None and not fence.contains(pose):
            await self._trip("safety.geofence", pose=pose.model_dump(), estop=self.limits.estop_on_geofence)
        battery = await self.robot.get_battery()
        if battery < self.limits.min_battery_pct:
            await self._trip("safety.battery_low", battery_pct=battery, min_pct=self.limits.min_battery_pct)

    async def _trip(self, kind: str, estop: bool = False, **data: Any) -> None:
        self.violations.append({"kind": kind, **data})
        self.log.emit(kind, **data)
        if estop:
            await self.robot.estop()
        else:
            await self.robot.stop()

    async def _loop(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.period_s)
