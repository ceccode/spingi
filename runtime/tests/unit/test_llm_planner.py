"""LLMPlanner with a fake client: request shape, validation, retry with errors, refusals. No network."""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from spingi.core.plan import validate_plan
from spingi.planner.evaluate import equivalence, evaluate, golden_plan, load_cases
from spingi.planner.llm import FALLBACK_BETA, LLMPlanner, PlanningError, describe_world
from spingi.planner.schema import plan_schema
from spingi.scenes import load_routes, load_world
from spingi.skills import default_registry

CASES = Path("plans/golden/planner_cases.yaml")
WAREHOUSE = Path("sim/scenes/warehouse_small.yaml")


@dataclass
class Block:
    text: str
    type: str = "text"


@dataclass
class Usage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class Response:
    content: list[Any]
    stop_reason: str = "end_turn"
    model: str = "claude-opus-5-5"
    usage: Usage = field(default_factory=Usage)
    stop_details: Any = None


class FakeClient:
    """Answers with queued plans; records every request it receives."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict] = []
        self.beta = self
        self.messages = self

    def create(self, **request):
        self.requests.append(copy.deepcopy(request))
        answer = self.answers.pop(0)
        if isinstance(answer, Response):
            return answer
        return Response(content=[Block(json.dumps(answer))])


def golden_as_answer(case_id: str) -> dict:
    case = next(c for c in load_cases(CASES) if c.id == case_id)
    plan = golden_plan(case)
    steps = []
    for s in plan.steps:
        params = {name: s.params.get(name) for name in default_registry().get(s.skill).Params.model_fields}
        steps.append({"skill": s.skill, "params": params, "on_failure": {"retry": 0, "then": "needs_human"}})
    return {"id": case.id, "description": case.request, "steps": steps}


def planner(client) -> LLMPlanner:
    return LLMPlanner(default_registry(), client=client)


def test_request_uses_structured_output_and_fallbacks_without_forced_tools():
    client = FakeClient(golden_as_answer("go_to_workstation"))
    planner(client).plan("Go to workstation B.", load_world(WAREHOUSE), load_routes(WAREHOUSE))
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["output_config"]["effort"] == "medium"
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert "tool_choice" not in req and "thinking" not in req and "tools" not in req
    user = req["messages"][0]["content"]
    assert "Request: Go to workstation B." in user and "dock->workstation_B: aisle_in, aisle_out" in user
    assert "red_box_01, class red_box" in user


def test_schema_only_allows_whitelisted_skills_and_strict_objects():
    schema = plan_schema(default_registry())
    variants = schema["properties"]["steps"]["items"]["anyOf"]
    assert sorted(v["properties"]["skill"]["const"] for v in variants) == default_registry().names()

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            for bad in ("minLength", "maximum", "pattern", "default"):
                assert bad not in node
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(schema)


def test_valid_answer_becomes_a_validated_plan_with_defaults_restored():
    client = FakeClient(golden_as_answer("fetch_and_deliver"))
    result = planner(client).plan("Bring the red box", load_world(WAREHOUSE), load_routes(WAREHOUSE))
    assert result.attempts == 1 and validate_plan(result.plan, default_registry()) == []
    assert "max_speed" not in result.plan.steps[0].params  # null in the answer -> skill default applies
    case = next(c for c in load_cases(CASES) if c.id == "fetch_and_deliver")
    assert equivalence(result.plan, case, default_registry()) == []


def test_invalid_plan_is_sent_back_with_the_errors_append_only():
    bad = golden_as_answer("fetch_and_deliver")
    bad["steps"][2]["params"]["object_id"] = "$place.objects[0].id"  # references a later step
    first = Response(content=[Block(json.dumps(bad))])
    client = FakeClient(first, golden_as_answer("fetch_and_deliver"))
    result = planner(client).plan("Bring the red box", load_world(WAREHOUSE), load_routes(WAREHOUSE))
    assert result.attempts == 2
    second = client.requests[1]["messages"]
    assert second[0] == client.requests[0]["messages"][0]  # history unchanged
    assert second[1]["role"] == "assistant" and second[1]["content"] == first.content
    assert "does not point to a previous step" in second[2]["content"]


def test_two_invalid_answers_raise_and_nothing_is_returned():
    client = FakeClient(
        {"id": "x", "description": "", "steps": [{"skill": "fly", "params": {}, "on_failure": {}}]}, "not json at all"
    )
    with pytest.raises(PlanningError, match="no valid plan after 2 attempts"):
        planner(client).plan("Fly", load_world(WAREHOUSE))


def test_refusal_and_truncation_are_planning_errors():
    for stop in ("refusal", "max_tokens"):
        client = FakeClient(Response(content=[], stop_reason=stop))
        with pytest.raises(PlanningError):
            planner(client).plan("anything", load_world(WAREHOUSE))


def test_world_description_lists_only_symbolic_state():
    text = describe_world(load_world(WAREHOUSE), load_routes(WAREHOUSE))
    assert "aisle_in" in text and "battery 100%" in text and "Holding: nothing" in text


def test_every_golden_plan_is_valid_and_equivalent_to_itself():
    reg = default_registry()
    for case in load_cases(CASES):
        plan = golden_plan(case)
        assert validate_plan(plan, reg) == [], case.id
        assert equivalence(plan, case, reg) == [], case.id


def test_equivalence_reports_differences():
    reg = default_registry()
    case = next(c for c in load_cases(CASES) if c.id == "fetch_and_deliver")
    plan = golden_plan(case)
    plan.steps[3].params["via"] = []  # straight line through the rack
    assert any("via" in d for d in equivalence(plan, case, reg))
    plan.steps[2].params["object_id"] = "$1.objects[0].id"  # same reference, by index
    plan.steps[3].params["via"] = ["aisle_out"]
    assert equivalence(plan, case, reg) == []


def test_evaluate_with_a_perfect_fake_planner_passes_all_ten_cases():
    cases = load_cases(CASES)
    assert len(cases) == 10
    client = FakeClient(*[golden_as_answer(c.id) for c in cases])
    p = planner(client)

    def plan_fn(case):
        r = p.plan(case.request, load_world(case.scene), load_routes(case.scene))
        return r.plan, r.attempts

    report = evaluate(cases, plan_fn, default_registry(), p.model)
    assert report.passed == 10 and report.pass_rate == 1.0
