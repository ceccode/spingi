"""Task Executor: runs a TaskPlan one step at a time (spec, section 4).

step -> preconditions -> execute with deadline -> postconditions -> next / retry / escalation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from spingi.core.events import EventLog
from spingi.core.plan import PlanError, TaskPlan, resolve_params, validate_plan
from spingi.core.ports import Clock, HumanGateway, HumanRequest, Perceiver, RobotAdapter, RobotEstopped
from spingi.core.skill import SkillContext, SkillOutcome, SkillRegistry, SkillResult
from spingi.core.types import WorldState, apply_delta

RunStatus = Literal["success", "aborted", "invalid_plan", "deadline", "estop", "error"]

# An operator may answer "retry" to a failing step at most this many times; then the run is aborted. Without a cap a
# scripted or tired operator could loop forever on a step that cannot succeed.
MAX_OPERATOR_RETRIES = 3


class RunResult(BaseModel):
    run_id: str
    status: RunStatus
    final_state: WorldState
    steps_completed: int
    reason: str = ""
    outputs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "success"


class Executor:
    def __init__(
        self,
        registry: SkillRegistry,
        robot: RobotAdapter,
        perceiver: Perceiver,
        human: HumanGateway,
        log: EventLog | None = None,
        human_timeout_s: float = 300.0,
        clock=time.monotonic,
        deadline_clock: Clock | None = None,
    ) -> None:
        self.registry = registry
        self.robot = robot
        self.perceiver = perceiver
        self.human = human
        self.log = log or EventLog(run_id=f"r-{uuid.uuid4().hex[:8]}")
        self.human_timeout_s = human_timeout_s
        self._clock = clock
        self._deadline_clock = deadline_clock  # robot time for step deadlines (simulated time in fast simulation)
        if self.log.sim_clock is None and hasattr(robot, "sim_time_s"):
            self.log.sim_clock = lambda: robot.sim_time_s

    async def run(self, plan: TaskPlan, state: WorldState) -> RunResult:
        run_id = self.log.run_id
        self.log.emit("run.start", plan_id=plan.id, steps=len(plan.steps))

        errors = validate_plan(plan, self.registry)
        if errors:
            self.log.emit("plan.invalid", errors=errors)
            self.log.emit("run.end", status="invalid_plan")
            return RunResult(
                run_id=run_id,
                status="invalid_plan",
                final_state=state,
                steps_completed=0,
                reason="; ".join(errors),
            )
        self.log.emit("plan.validated", plan_id=plan.id)

        started = self._clock()
        outputs: list[dict[str, Any]] = []

        for index, step in enumerate(plan.steps):
            if self.robot.estopped:
                return await self._finish(state, index, outputs, "estop", "robot e-stopped")
            if plan.deadline_s is not None and self._clock() - started > plan.deadline_s:
                return await self._finish(state, index, outputs, "deadline", "run budget exhausted")

            skill = self.registry.get(step.skill)
            try:
                raw_params = resolve_params(index, plan, outputs)
                params = skill.Params.model_validate(raw_params)
            except (PlanError, ValueError) as exc:
                decision = await self._escalate(index, step.skill, str(exc), step.on_failure.then)
                if decision == "skip":
                    outputs.append({})
                    continue
                return await self._finish(state, index, outputs, "aborted", str(exc))

            self.log.emit("step.start", index=index, skill=step.skill, params=raw_params)
            attempt = 0
            operator_retries = 0
            step_done = False
            while not step_done:
                await asyncio.sleep(0)  # let signal handlers and the safety monitor run between attempts
                check = skill.preconditions(params, state)
                if not check.ok:
                    # Nothing has happened since the last check, so a retry would fail the same way:
                    # go straight to the step's escalation policy.
                    self.log.emit("skill.precondition_failed", skill=step.skill, reason=check.reason)
                    result = SkillResult.needs_human(f"precondition: {check.reason}")
                else:
                    deadline = step.deadline_s if step.deadline_s is not None else skill.default_deadline_s
                    plan_left = None if plan.deadline_s is None else plan.deadline_s - (self._clock() - started)
                    plan_limited = plan_left is not None and plan_left < deadline
                    if plan_limited:
                        deadline = max(plan_left, 0.0)
                    ctx = SkillContext(
                        state=state,
                        robot=self.robot,
                        perceiver=self.perceiver,
                        log=self.log,
                        human=self.human,
                        deadline_s=deadline,
                    )
                    self.log.emit("skill.start", skill=step.skill, attempt=attempt)
                    t0 = self._clock()
                    result = await self._execute_with_deadline(skill, params, ctx, deadline)
                    self.log.emit(
                        "skill.end",
                        skill=step.skill,
                        attempt=attempt,
                        outcome=result.outcome.value,
                        reason=result.reason,
                        duration_s=round(self._clock() - t0, 4),
                    )
                    if self.robot.estopped:  # e-stopped during the skill (monitor, operator, adapter): terminal
                        return await self._finish(state, index, outputs, "estop", "robot e-stopped")
                    if plan_limited and result.reason.startswith("deadline"):
                        return await self._finish(state, index, outputs, "deadline", "run budget exhausted")
                    if result.outcome is SkillOutcome.SUCCESS:
                        candidate = apply_delta(state, result.delta)
                        post = skill.postconditions(params, candidate)
                        if post.ok:
                            state = candidate
                            if result.delta is not None:
                                self.log.emit(
                                    "state.delta", skill=step.skill, delta=result.delta.model_dump(exclude_none=True)
                                )
                            outputs.append(result.evidence)
                            self.log.emit("step.end", index=index, skill=step.skill, outcome="success")
                            step_done = True
                            continue
                        self.log.emit("skill.postcondition_failed", skill=step.skill, reason=post.reason)
                        result = SkillResult.recoverable(f"postcondition: {post.reason}")

                if result.outcome is SkillOutcome.FATAL:
                    await self.robot.estop()  # act first, then record
                    self.log.emit("safety.estop", skill=step.skill, reason=result.reason)
                    return await self._finish(state, index, outputs, "estop", f"fatal: {result.reason}")

                if result.outcome is SkillOutcome.RECOVERABLE and attempt < step.on_failure.retry:
                    attempt += 1
                    self.log.emit("step.retry", index=index, skill=step.skill, attempt=attempt)
                    continue

                decision = await self._escalate(index, step.skill, result.reason, step.on_failure.then)
                if decision == "retry":
                    operator_retries += 1
                    if operator_retries > MAX_OPERATOR_RETRIES:
                        reason = f"{MAX_OPERATOR_RETRIES} operator retries exhausted: {result.reason}"
                        return await self._finish(state, index, outputs, "aborted", reason)
                    attempt = 0
                    continue
                if decision == "skip":
                    outputs.append({})
                    self.log.emit("step.end", index=index, skill=step.skill, outcome="skipped")
                    step_done = True
                    continue
                return await self._finish(state, index, outputs, "aborted", result.reason)

        return await self._finish(state, len(plan.steps), outputs, "success", "")

    async def _execute_with_deadline(self, skill, params, ctx: SkillContext, deadline: float) -> SkillResult:
        """Runs the skill until it ends, its deadline passes in robot time, or the same deadline passes in wall-clock
        time (a guard against a skill or adapter that hangs without the robot's clock moving)."""
        work = asyncio.ensure_future(skill.execute(params, ctx))
        timers = []
        if self._deadline_clock is not None:
            timers.append(asyncio.ensure_future(self._deadline_clock.sleep(deadline)))
        try:
            done, _ = await asyncio.wait({work, *timers}, timeout=deadline, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in timers:
                t.cancel()
        if work not in done:
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await work
            await skill.abort()
            await self.robot.stop()
            self.log.emit("skill.deadline", skill=skill.name, deadline_s=deadline)
            return SkillResult.recoverable(f"deadline of {deadline:g}s exceeded")
        try:
            return work.result()
        except RobotEstopped:
            return SkillResult.fatal("robot e-stopped")
        except Exception as exc:  # noqa: BLE001 - a bug in a skill must not leave the robot moving
            await self.robot.stop()
            self.log.emit("skill.exception", skill=skill.name, error=repr(exc))
            return SkillResult.needs_human(f"exception in {skill.name}: {exc!r}")

    async def _escalate(self, index: int, skill: str, reason: str, policy: str) -> str:
        """Applies on_failure.then. Returns 'retry' | 'skip' | 'abort'."""
        if policy == "abort":
            self.log.emit("step.abort", index=index, skill=skill, reason=reason)
            return "abort"
        if policy == "skip":
            self.log.emit("step.skip", index=index, skill=skill, reason=reason)
            return "skip"
        await self.robot.stop()
        request = HumanRequest(run_id=self.log.run_id, step_index=index, skill=skill, reason=reason)
        self.log.emit("human.request", index=index, skill=skill, reason=reason)
        try:
            response = await self.human.ask(request, timeout_s=self.human_timeout_s)
        except TimeoutError:
            self.log.emit("human.timeout", index=index, skill=skill)
            return "abort"
        self.log.emit("human.response", index=index, action=response.action, note=response.note)
        return response.action if response.action in ("retry", "skip", "abort") else "abort"

    async def _finish(self, state, completed: int, outputs, status: RunStatus, reason: str) -> RunResult:
        if status != "success" and not self.robot.estopped:
            await self.robot.stop()
        self.log.emit("run.end", status=status, steps_completed=completed, reason=reason)
        return RunResult(
            run_id=self.log.run_id,
            status=status,
            final_state=state,
            steps_completed=completed,
            reason=reason,
            outputs=outputs,
        )
