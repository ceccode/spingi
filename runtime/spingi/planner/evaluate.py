"""Planner evaluation against golden plans (runtime-spec milestone M3).

`equivalence(plan, golden)` lists the differences between a produced plan and the expected one; an empty list
means equivalent. `evaluate()` runs a planner on every case of a cases file and reports the pass rate.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from spingi.core.plan import Step, TaskPlan, parse_ref
from spingi.core.skill import SkillRegistry

IGNORED_PARAMS = {"say": {"text"}, "wait_for_human": {"prompt", "timeout_s"}}


class GoldenCase(BaseModel):
    id: str
    scene: str
    request: str
    steps: list[dict[str, Any]]


class CaseResult(BaseModel):
    id: str
    request: str
    passed: bool
    differences: list[str] = Field(default_factory=list)
    error: str | None = None
    plan: dict[str, Any] | None = None
    attempts: int = 0
    seconds: float = 0.0


class PlannerReport(BaseModel):
    model: str
    cases: int
    passed: int
    pass_rate: float
    results: list[CaseResult]


def load_cases(path: Path | str) -> list[GoldenCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [GoldenCase.model_validate(c) for c in raw.get("cases", [])]


def golden_plan(case: GoldenCase) -> TaskPlan:
    """The golden steps as a runnable plan (used to check that every golden plan is itself valid)."""
    steps = []
    for s in case.steps:
        params = dict(s.get("params") or {})
        if s["skill"] == "say":
            params.setdefault("text", "...")
        if s["skill"] == "wait_for_human":
            params.setdefault("prompt", "Confirm, then continue")
        steps.append(Step(skill=s["skill"], params=params))
    return TaskPlan(id=case.id, description=case.request, steps=steps)


def equivalence(plan: TaskPlan, case: GoldenCase, registry: SkillRegistry) -> list[str]:
    diffs: list[str] = []
    got = [s.skill for s in plan.steps]
    want = [s["skill"] for s in case.steps]
    if got != want:
        return [f"skills {got} instead of {want}"]
    for i, (step, expected) in enumerate(zip(plan.steps, case.steps, strict=True)):
        skill = registry.get(step.skill)
        defaults = {name: f.get_default(call_default_factory=True) for name, f in skill.Params.model_fields.items()}
        expected_params = expected.get("params") or {}
        ignored = IGNORED_PARAMS.get(step.skill, set())
        for name in skill.Params.model_fields:
            if name in ignored:
                continue
            actual = step.params.get(name, defaults[name])
            wanted = expected_params.get(name, defaults[name])
            if _normalize(actual, plan, i) != _normalize(wanted, plan, i):
                diffs.append(f"step {i} {step.skill}.{name}: {actual!r} instead of {wanted!r}")
    return diffs


def _normalize(value: Any, plan: TaskPlan, index: int) -> Any:
    """References by skill name or by index become the same thing; check lists compare as sets."""
    ref = parse_ref(value)
    if ref is not None:
        target, path = ref
        if target.isdigit():
            target = plan.steps[int(target)].skill if int(target) < index else target
        return ("ref", target, re.sub(r"\s+", "", path))
    if isinstance(value, list) and all(isinstance(v, str) and ":" in v for v in value):
        return sorted(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def evaluate(
    cases: list[GoldenCase],
    plan_fn: Callable[[GoldenCase], tuple[TaskPlan, int]],
    registry: SkillRegistry,
    model: str,
    progress: Callable[[CaseResult], None] | None = None,
) -> PlannerReport:
    results = []
    for case in cases:
        t0 = time.monotonic()
        try:
            plan, attempts = plan_fn(case)
            diffs = equivalence(plan, case, registry)
            result = CaseResult(
                id=case.id,
                request=case.request,
                passed=not diffs,
                differences=diffs,
                plan=plan.model_dump(),
                attempts=attempts,
            )
        except Exception as exc:  # noqa: BLE001 - a planning failure is a failed case, not a crashed evaluation
            result = CaseResult(id=case.id, request=case.request, passed=False, error=f"{type(exc).__name__}: {exc}")
        result.seconds = round(time.monotonic() - t0, 2)
        results.append(result)
        if progress:
            progress(result)
    passed = sum(r.passed for r in results)
    return PlannerReport(
        model=model,
        cases=len(results),
        passed=passed,
        pass_rate=round(passed / len(results), 3) if results else 0.0,
        results=results,
    )
