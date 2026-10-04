"""JSON schema of a TaskPlan built from the skill registry, for structured outputs (ADR-0003, ADR-0010).

Each step is an `anyOf` variant per whitelisted skill, so the model can only name existing skills and fill their
parameters with the right shape. Constraints structured outputs do not support (lengths, ranges, patterns) are
stripped here and enforced afterwards by `validate_plan`, which every plan goes through anyway.
"""

from __future__ import annotations

import re
from typing import Any

from spingi.core.skill import SkillRegistry

_DROP = {
    "title",
    "default",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "pattern",
    "minItems",
    "maxItems",
}


def _clean(prop: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    alternatives = re.fullmatch(r"\^\(([\w|]+)\)\$", prop.get("pattern", ""))
    if alternatives:  # a pattern like ^(left|right)$ is an enum in disguise: keep it as one
        out["enum"] = alternatives.group(1).split("|")
    for key, value in prop.items():
        if key in _DROP:
            continue
        if key == "items" and isinstance(value, dict):
            out[key] = _clean(value)
        elif key == "anyOf":
            out[key] = [_clean(v) for v in value]
        else:
            out[key] = value
    return out


def params_schema(model_schema: dict[str, Any]) -> dict[str, Any]:
    """Strict object schema: every property listed in `required`, optional ones nullable (null = use the default)."""
    required = set(model_schema.get("required", []))
    props: dict[str, Any] = {}
    for name, prop in model_schema.get("properties", {}).items():
        p = _clean(prop)
        if name not in required:
            description = p.pop("description", None)
            p = {"anyOf": [p, {"type": "null"}]}
            if description:
                p["description"] = f"{description} (null for the default)"
        props[name] = p
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


ON_FAILURE = {
    "type": "object",
    "description": "How many times to retry this step, then what to do",
    "properties": {
        "retry": {"type": "integer", "description": "0 to 10; usually 0 to 2"},
        "then": {"type": "string", "enum": ["needs_human", "abort", "skip"]},
    },
    "required": ["retry", "then"],
    "additionalProperties": False,
}


def plan_schema(registry: SkillRegistry) -> dict[str, Any]:
    variants = []
    for name in registry.names():
        skill = registry.get(name)
        variants.append(
            {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "const": name},
                    "params": params_schema(skill.Params.model_json_schema()),
                    "on_failure": ON_FAILURE,
                },
                "required": ["skill", "params", "on_failure"],
                "additionalProperties": False,
            }
        )
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "snake_case identifier, at most 64 characters"},
            "description": {"type": "string", "description": "One sentence: what the plan does"},
            "steps": {"type": "array", "items": {"anyOf": variants}},
        },
        "required": ["id", "description", "steps"],
        "additionalProperties": False,
    }


def drop_nulls(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Nullable optional parameters come back as null: remove them so the skill defaults apply."""
    steps = []
    for step in raw_plan.get("steps", []):
        params = {k: v for k, v in (step.get("params") or {}).items() if v is not None}
        steps.append({**step, "params": params})
    return {**raw_plan, "steps": steps}
