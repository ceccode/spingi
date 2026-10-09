"""TaskPlan: declarative list of steps, no control flow (ADR-0004)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from spingi.core.ports import Capability
from spingi.core.skill import SkillRegistry

REF_PATTERN = re.compile(r"^\$(?P<step>[A-Za-z_][A-Za-z0-9_]*|\d+)\.(?P<path>.+)$")


class OnFailure(BaseModel):
    retry: int = Field(default=0, ge=0, le=10)
    then: Literal["needs_human", "abort", "skip"] = "needs_human"


class Step(BaseModel):
    skill: str
    params: dict[str, Any] = Field(default_factory=dict)
    on_failure: OnFailure = Field(default_factory=OnFailure)
    deadline_s: float | None = Field(default=None, gt=0, description="wall-clock seconds; default: the skill's")


class TaskPlan(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = ""
    steps: list[Step] = Field(min_length=1)
    deadline_s: float | None = Field(default=None, gt=0, description="robot-time budget for the whole run")


class PlanError(ValueError):
    pass


def load_plan(path: Path | str) -> TaskPlan:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return TaskPlan.model_validate(raw)
    except ValidationError as exc:
        raise PlanError(f"invalid plan ({path}): {exc}") from exc


def validate_plan(
    plan: TaskPlan, registry: SkillRegistry, capabilities: frozenset[Capability] | None = None
) -> list[str]:
    """Returns the list of errors. Empty = valid plan.

    Checks: every skill exists; the robot has the capabilities every skill needs (when `capabilities` is given);
    every params passes the skill schema (`$...` references are replaced by a placeholder before validation);
    every reference points to a previous step.
    """
    errors: list[str] = []
    for i, step in enumerate(plan.steps):
        if step.skill not in registry:
            errors.append(f"step {i}: unknown skill '{step.skill}'")
            continue
        missing = registry.get(step.skill).missing(capabilities) if capabilities is not None else []
        if missing:
            errors.append(f"step {i}: skill '{step.skill}' needs {', '.join(missing)}, which this robot does not have")
            continue
        for key, value in step.params.items():
            ref = parse_ref(value)
            if ref is None:
                continue
            target, _ = ref
            if not _ref_points_backwards(target, i, plan):
                errors.append(f"step {i}: reference '{value}' in '{key}' does not point to a previous step")
        skill = registry.get(step.skill)
        try:
            skill.Params.model_validate(_with_placeholders(step.params))
        except ValidationError as exc:
            errors.append(f"step {i} ({step.skill}): invalid parameters: {_short(exc)}")
    return errors


def parse_ref(value: Any) -> tuple[str, str] | None:
    """'$detect.objects[0].id' -> ('detect', 'objects[0].id'); None if it is not a reference."""
    if not isinstance(value, str):
        return None
    match = REF_PATTERN.match(value)
    if match is None:
        return None
    return match.group("step"), match.group("path")


def resolve_params(step_index: int, plan: TaskPlan, outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Replaces references with values taken from the evidence of previous steps."""
    step = plan.steps[step_index]
    resolved: dict[str, Any] = {}
    for key, value in step.params.items():
        ref = parse_ref(value)
        if ref is None:
            resolved[key] = value
            continue
        target, path = ref
        source_index = _resolve_step_index(target, step_index, plan)
        if source_index is None or source_index >= len(outputs):
            raise PlanError(f"step {step_index}: reference '{value}' cannot be resolved")
        resolved[key] = _dig(outputs[source_index], path, value)
    return resolved


def _resolve_step_index(target: str, current: int, plan: TaskPlan) -> int | None:
    if target.isdigit():
        idx = int(target)
        return idx if idx < current else None
    for idx in range(current - 1, -1, -1):
        if plan.steps[idx].skill == target:
            return idx
    return None


def _ref_points_backwards(target: str, current: int, plan: TaskPlan) -> bool:
    return _resolve_step_index(target, current, plan) is not None


def _dig(data: Any, path: str, original: str) -> Any:
    """Walks evidence with dict keys and list indexes only: no attributes, no private names."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]", path)
    cur = data
    for tok in tokens:
        if tok.startswith("_"):
            raise PlanError(f"reference '{original}': field '{tok}' is not allowed")
        try:
            if tok.startswith("["):
                if not isinstance(cur, list):
                    raise TypeError("not a list")
                cur = cur[int(tok[1:-1])]
            else:
                if not isinstance(cur, dict):
                    raise TypeError("not a mapping")
                cur = cur[tok]
        except (KeyError, IndexError, TypeError) as exc:
            raise PlanError(f"reference '{original}': field '{tok}' missing") from exc
    return cur


def _with_placeholders(params: dict[str, Any]) -> dict[str, Any]:
    return {k: ("__ref__" if parse_ref(v) is not None else v) for k, v in params.items()}


def _short(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
