"""Contract of a skill: an atomic action with verifiable preconditions and postconditions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from spingi.core.events import EventLog
from spingi.core.ports import HumanGateway, Perceiver, RobotAdapter
from spingi.core.types import StateDelta, WorldState


class SkillOutcome(StrEnum):
    SUCCESS = "success"
    RECOVERABLE = "recoverable"  # retry, possibly with different parameters
    NEEDS_HUMAN = "needs_human"  # stop and ask
    FATAL = "fatal"  # stop everything, do not retry


class SkillResult(BaseModel):
    outcome: SkillOutcome
    delta: StateDelta | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, delta: StateDelta | None = None, **evidence: Any) -> SkillResult:
        return cls(outcome=SkillOutcome.SUCCESS, delta=delta, evidence=evidence)

    @classmethod
    def recoverable(cls, reason: str, **evidence: Any) -> SkillResult:
        return cls(outcome=SkillOutcome.RECOVERABLE, reason=reason, evidence=evidence)

    @classmethod
    def needs_human(cls, reason: str, **evidence: Any) -> SkillResult:
        return cls(outcome=SkillOutcome.NEEDS_HUMAN, reason=reason, evidence=evidence)

    @classmethod
    def fatal(cls, reason: str, **evidence: Any) -> SkillResult:
        return cls(outcome=SkillOutcome.FATAL, reason=reason, evidence=evidence)


class Check(BaseModel):
    ok: bool
    reason: str = ""

    @classmethod
    def passed(cls) -> Check:
        return cls(ok=True)

    @classmethod
    def failed(cls, reason: str) -> Check:
        return cls(ok=False, reason=reason)


class SkillContext(BaseModel):
    """Everything a skill may touch during execute. Nothing else."""

    model_config = {"arbitrary_types_allowed": True}

    state: WorldState
    robot: RobotAdapter
    perceiver: Perceiver
    log: EventLog
    human: HumanGateway | None = None
    deadline_s: float


class Skill:
    """Base class. Subclasses declare `name`, `Params` and implement the three methods.

    - preconditions/postconditions are PURE: they read the state, they do not touch the robot.
    - execute is the only place that talks to RobotAdapter and Perceiver.
    - a skill does not call another skill: composition lives in the plan (ADR-0004).
    """

    name: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    default_deadline_s: ClassVar[float] = 30.0

    def preconditions(self, params: BaseModel, state: WorldState) -> Check:
        return Check.passed()

    async def execute(self, params: BaseModel, ctx: SkillContext) -> SkillResult:
        raise NotImplementedError

    def postconditions(self, params: BaseModel, state: WorldState) -> Check:
        return Check.passed()

    async def abort(self) -> None:
        return None


class SkillRegistry:
    """Whitelist of the available skills. The Planner may use only these (ADR-0003)."""

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def names(self) -> list[str]:
        return sorted(self._skills)

    def schemas(self) -> dict[str, dict[str, Any]]:
        """JSON schema of each skill's parameters: this is what the LLM Planner sees."""
        return {name: s.Params.model_json_schema() for name, s in self._skills.items()}
