import pytest

from spingi.core.plan import PlanError, Step, TaskPlan, load_plan, resolve_params, validate_plan


def plan(*steps: dict) -> TaskPlan:
    return TaskPlan(id="t", steps=[Step(**s) for s in steps])


def test_valid_plan_has_no_errors(registry):
    p = plan({"skill": "navigate", "params": {"to": "shelf_A"}}, {"skill": "say", "params": {"text": "ok"}})
    assert validate_plan(p, registry) == []


def test_unknown_skill_is_rejected(registry):
    errors = validate_plan(plan({"skill": "fly", "params": {}}), registry)
    assert errors and "unknown skill" in errors[0]


def test_bad_params_are_rejected_before_running(registry):
    errors = validate_plan(plan({"skill": "navigate", "params": {"to": "A", "max_speed": 9}}), registry)
    assert errors and "max_speed" in errors[0]


def test_reference_must_point_backwards(registry):
    p = plan(
        {"skill": "say", "params": {"text": "$navigate.reached"}}, {"skill": "navigate", "params": {"to": "shelf_A"}}
    )
    errors = validate_plan(p, registry)
    assert errors and "does not point to a previous step" in errors[0]


def test_reference_by_skill_name_and_index_resolves():
    p = plan(
        {"skill": "navigate", "params": {"to": "shelf_A"}},
        {"skill": "say", "params": {"text": "$navigate.reached"}},
        {"skill": "say", "params": {"text": "$0.reached"}},
    )
    outputs = [{"reached": "shelf_A", "items": [{"id": "x"}]}]
    assert resolve_params(1, p, outputs)["text"] == "shelf_A"
    assert resolve_params(2, p, outputs)["text"] == "shelf_A"


def test_reference_with_index_path():
    p = plan({"skill": "detect", "params": {}}, {"skill": "pick", "params": {"object_id": "$detect.objects[0].id"}})
    assert resolve_params(1, p, [{"objects": [{"id": "red_box_01"}]}])["object_id"] == "red_box_01"


def test_missing_field_in_reference_raises():
    p = plan({"skill": "detect", "params": {}}, {"skill": "pick", "params": {"object_id": "$detect.objects[0].id"}})
    with pytest.raises(PlanError):
        resolve_params(1, p, [{"objects": []}])


def test_plan_requires_at_least_one_step():
    with pytest.raises(ValueError):
        TaskPlan(id="empty", steps=[])


def test_demo_plan_file_loads_and_validates(registry):
    p = load_plan("plans/demo_inspection_round.yaml")
    assert len(p.steps) == 9
    assert validate_plan(p, registry) == []
