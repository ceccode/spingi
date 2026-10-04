"""SafetyMonitor: observes the robot independently of the Executor and stops it when a limit is crossed.

It never plans and never recovers: it stops, emits an event and leaves the rest to the Executor
(postconditions fail, the step is retried or escalated). It also feeds the adapter watchdog.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from spingi.core.events import EventLog
from spingi.core.ports import Clock, RobotAdapter
from spingi.safety.limits import SafetyLimits


class SafetyMonitor:
    def __init__(
        self,
        robot: RobotAdapter,
        log: EventLog,
        limits: SafetyLimits,
        period_s: float = 0.05,
        clock: Clock | None = None,
    ) -> None:
        if period_s <= 0:
            raise ValueError("period_s must be > 0: a zero period would busy-loop the event loop")
        self.robot = robot
        self.log = log
        self.limits = limits
        self.period_s = period_s  # seconds of robot time between checks
        self.clock = clock or robot.clock  # robot time: wall clock on hardware, simulated time in simulation
        self._task: asyncio.Task[None] | None = None
        self.violations: list[dict[str, Any]] = []
        self.failed: str | None = None  # set when a check raised; the robot was e-stopped
        self._active: set[str] = set()  # violations in progress (recorded once each)
        self.checks = 0

    @property
    def tripped(self) -> bool:
        return bool(self.violations) or self.failed is not None

    async def start(self) -> None:
        await self.robot.heartbeat()  # the watchdog must not fire before the first loop iteration
        if self.limits.max_speed is not None:
            applied = self.robot.set_speed_limit(self.limits.max_speed)
            self.log.emit("safety.speed_capped", limit=self.limits.max_speed, applied=applied)
        self.log.emit("safety.armed", limits=self.limits.model_dump(exclude_none=True), period_s=self.period_s)
        self._task = asyncio.create_task(self._loop(), name="safety-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.log.emit("safety.disarmed", checks=self.checks, violations=len(self.violations))

    async def check_once(self) -> None:
        """One pass of the checks. Public so tests can drive it without the loop."""
        self.checks += 1
        await self.robot.heartbeat()
        pose = await self.robot.get_pose()
        fence = self.limits.geofence
        outside = fence is not None and not fence.contains(pose)
        await self._condition("safety.geofence", outside, self.limits.estop_on_geofence, pose=pose.model_dump())
        battery = await self.robot.get_battery()
        low = battery < self.limits.min_battery_pct
        minimum = self.limits.min_battery_pct
        await self._condition("safety.battery_low", low, False, battery_pct=battery, min_pct=minimum)

    async def _condition(self, kind: str, violated: bool, estop: bool, **data: Any) -> None:
        """Stops the robot on every check while violated, but records the violation once per occurrence."""
        if not violated:
            self._active.discard(kind)
            return
        if estop:  # act first, then record: a failing log must never delay the stop
            await self.robot.estop()
        else:
            await self.robot.stop()
        if kind not in self._active:
            self._active.add(kind)
            self.violations.append({"kind": kind, **data})
            self.log.emit(kind, **data)

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 - fail safe: a monitor that cannot check must stop the robot
                self.failed = repr(exc)
                with contextlib.suppress(Exception):
                    await self.robot.estop()
                with contextlib.suppress(Exception):
                    self.log.emit("safety.monitor_error", error=self.failed)
                return
            await self.clock.sleep(self.period_s)
