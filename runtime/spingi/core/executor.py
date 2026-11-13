"""Task Executor: runs a TaskPlan one step at a time (spec, section 4).

step -> preconditions -> execute with deadline -> postconditions -> next / retry / escalation.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field

from spingi.core.events import EventLog
from spingi.core.plan import PlanError, Step, TaskPlan, resolve_params, validate_plan
from spingi.core.ports import Clock, HumanGateway, HumanRequest, Perceiver, RobotAdapter, RobotEstopped, RobotFell
from spingi.core.skill import Skill, SkillContext, SkillOutcome, SkillRegistry, SkillResult
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


class _Run:
    """What a run carries from one step to the next."""

    def __init__(self, plan: TaskPlan, state: WorldState, started: float) -> None:
        self.plan = plan
        self.state = state
        self.started = started  # on the executor's clock: the run's budget counts from here
        self.outputs: list[dict[str, Any]] = []  # one per step done or skipped, for later steps' references


class _Stop(NamedTuple):
    """Why a run ends before its last step."""

    status: RunStatus
    reason: str


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
        self.log.emit("run.start", plan_id=plan.id, steps=len(plan.steps))

        errors = validate_plan(plan, self.registry, self.robot.capabilities)
        if errors:
            self.log.emit("plan.invalid", errors=errors)
            self.log.emit("run.end", status="invalid_plan")
            return RunResult(
                run_id=self.log.run_id,
                status="invalid_plan",
                final_state=state,
                steps_completed=0,
                reason="; ".join(errors),
            )
        self.log.emit("plan.validated", plan_id=plan.id)

        run = _Run(plan=plan, state=state, started=self._clock())
        for index, step in enumerate(plan.steps):
            stop = self._stop_before_step(run) or await self._run_step(run, index, step)
            if stop is not None:
                return await self._finish(run.state, index, run.outputs, stop.status, stop.reason)
        return await self._finish(run.state, len(plan.steps), run.outputs, "success", "")

    def _stop_before_step(self, run: _Run) -> _Stop | None:
        if self.robot.estopped:
            return _Stop("estop", "robot e-stopped")
        if run.plan.deadline_s is not None and self._clock() - run.started > run.plan.deadline_s:
            return _Stop("deadline", "run budget exhausted")
        return None

    async def _run_step(self, run: _Run, index: int, step: Step) -> _Stop | None:
        """Runs one step until it succeeds or is skipped (None: on to the next step), or until the run must end.

        A recoverable failure is tried again as many times as the step allows; then the step's escalation policy
        decides, which may ask the operator.
        """
        skill = self.registry.get(step.skill)
        try:
            raw_params = resolve_params(index, run.plan, run.outputs)
            params = skill.Params.model_validate(raw_params)
        except (PlanError, ValueError) as exc:
            decision = await self._escalate(index, step.skill, str(exc), step.on_failure.then)
            if decision == "skip":
                run.outputs.append({})
                return None
            return _Stop("aborted", str(exc))  # a retry would find the same parameters

        self.log.emit("step.start", index=index, skill=step.skill, params=raw_params)
        attempt = 0
        operator_retries = 0
        while True:
            await asyncio.sleep(0)  # let signal handlers and the safety monitor run between attempts
            result = await self._attempt(run, index, step, skill, params, attempt)
            if isinstance(result, _Stop):
                return result
            if result.outcome is SkillOutcome.SUCCESS:
                return None

            if result.outcome is SkillOutcome.FATAL:
                await self.robot.estop()  # act first, then record
                self.log.emit("safety.estop", skill=step.skill, reason=result.reason)
                return _Stop("estop", f"fatal: {result.reason}")

            if result.outcome is SkillOutcome.RECOVERABLE and attempt < step.on_failure.retry:
                attempt += 1
                self.log.emit("step.retry", index=index, skill=step.skill, attempt=attempt)
                continue

            decision = await self._escalate(index, step.skill, result.reason, step.on_failure.then)
            if decision == "retry":
                operator_retries += 1
                if operator_retries > MAX_OPERATOR_RETRIES:
                    return _Stop("aborted", f"{MAX_OPERATOR_RETRIES} operator retries exhausted: {result.reason}")
                attempt = 0
                continue
            if decision == "skip":
                run.outputs.append({})
                self.log.emit("step.end", index=index, skill=step.skill, outcome="skipped")
                return None
            return _Stop("aborted", result.reason)

    async def _attempt(
        self, run: _Run, index: int, step: Step, skill: Skill, params: BaseModel, attempt: int
    ) -> SkillResult | _Stop:
        """One try of a step: preconditions, the skill under its deadline, postconditions.

        A success is committed to the run (its state, the step's output) before it is returned. A failed pre or
        postcondition comes back as the failure the step's policy must handle. A _Stop ends the run.
        """
        check = skill.preconditions(params, run.state)
        if not check.ok:
            # Nothing has happened since the last check, so a retry would fail the same way:
            # go straight to the step's escalation policy.
            self.log.emit("skill.precondition_failed", skill=step.skill, reason=check.reason)
            return SkillResult.needs_human(f"precondition: {check.reason}")

        deadline, plan_limited = self._deadline_for(run, step, skill)
        ctx = SkillContext(
            state=run.state,
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
        if self.robot.estopped:  # e-stopped during the skill (monitor, operator, adapter, a fall): terminal
            fatal = result.outcome is SkillOutcome.FATAL and result.reason
            return _Stop("estop", fatal or "robot e-stopped")
        if plan_limited and result.reason.startswith("deadline"):
            return _Stop("deadline", "run budget exhausted")
        if result.outcome is not SkillOutcome.SUCCESS:
            return result

        candidate = apply_delta(run.state, result.delta)
        post = skill.postconditions(params, candidate)
        if not post.ok:
            self.log.emit("skill.postcondition_failed", skill=step.skill, reason=post.reason)
            return SkillResult.recoverable(f"postcondition: {post.reason}")
        run.state = candidate
        if result.delta is not None:
            self.log.emit("state.delta", skill=step.skill, delta=result.delta.model_dump(exclude_none=True))
        run.outputs.append(result.evidence)
        self.log.emit("step.end", index=index, skill=step.skill, outcome="success")
        return result

    def _deadline_for(self, run: _Run, step: Step, skill: Skill) -> tuple[float, bool]:
        """The seconds a try of the step may take, and whether it is what is left of the run's budget that limits
        it rather than the step's own deadline."""
        deadline = step.deadline_s if step.deadline_s is not None else skill.default_deadline_s
        if run.plan.deadline_s is None:
            return deadline, False
        plan_left = run.plan.deadline_s - (self._clock() - run.started)
        if plan_left < deadline:
            return max(plan_left, 0.0), True
        return deadline, False

    async def _execute_with_deadline(
        self, skill: Skill, params: BaseModel, ctx: SkillContext, deadline: float
    ) -> SkillResult:
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
        except RobotFell as exc:
            self.log.emit("robot.fell", skill=skill.name, reason=str(exc))
            return SkillResult.fatal(str(exc))
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
