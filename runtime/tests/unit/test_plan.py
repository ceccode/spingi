import pytest
from pydantic import ValidationError

from spingi.core.plan import PlanError, Step, TaskPlan, load_plan, resolve_params, validate_plan
from spingi.skills import default_registry


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


def test_a_misspelled_parameter_is_an_error_not_the_default(registry):
    errors = validate_plan(plan({"skill": "navigate", "params": {"to": "shelf_A", "max_sped": 0.8}}), registry)
    assert errors == ["step 0 (navigate): invalid parameters: max_sped: Extra inputs are not permitted"]


@pytest.mark.parametrize("skill", default_registry().names())
def test_every_skill_refuses_a_parameter_it_does_not_have(registry, skill):
    errors = validate_plan(plan({"skill": skill, "params": {"speed_limit": 1}}), registry)
    assert len(errors) == 1 and errors[0].startswith(f"step 0 ({skill}): invalid parameters: ")
    assert "speed_limit: Extra inputs are not permitted" in errors[0]


def test_a_misspelled_parameter_holding_a_reference_is_refused(registry):
    p = plan({"skill": "detect", "params": {"cls": "red_box"}}, {"skill": "pick", "params": {"objectid": "$0.id"}})
    errors = validate_plan(p, registry)
    assert len(errors) == 1 and "objectid: Extra inputs are not permitted" in errors[0]


@pytest.mark.parametrize(
    ("raw", "where"),
    [
        ({"id": "t", "dealine_s": 60, "steps": [{"skill": "say"}]}, "dealine_s"),
        ({"id": "t", "steps": [{"skill": "say", "on_failre": {"then": "abort"}}]}, "steps.0.on_failre"),
        ({"id": "t", "steps": [{"skill": "say", "on_failure": {"retires": 2}}]}, "steps.0.on_failure.retires"),
    ],
)
def test_a_misspelled_key_in_the_plan_is_an_error_not_the_default(raw, where):
    with pytest.raises(ValidationError) as exc:
        TaskPlan.model_validate(raw)
    (error,) = exc.value.errors()
    assert ".".join(str(p) for p in error["loc"]) == where and error["type"] == "extra_forbidden"


def test_a_plan_file_that_is_not_a_task_plan_is_one_line(tmp_path):
    path = tmp_path / "typo.yaml"
    path.write_text("id: typo\nsteps:\n  - skill: say\n    params: { text: hi }\n    on_failre: { then: abort }\n")
    with pytest.raises(PlanError) as exc:
        load_plan(path)
    assert str(exc.value) == f"invalid plan ({path}): steps.0.on_failre: Extra inputs are not permitted"
    path.write_text("")  # an empty file is not a mapping: the message has no location
    with pytest.raises(PlanError, match=r"\): Input should be a valid dictionary"):
        load_plan(path)


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


def test_references_only_walk_dict_keys_and_list_indexes():
    p = plan({"skill": "detect", "params": {}}, {"skill": "say", "params": {"text": "$detect.__class__"}})
    with pytest.raises(PlanError, match="not allowed"):
        resolve_params(1, p, [{"objects": []}])
    p = plan({"skill": "detect", "params": {}}, {"skill": "say", "params": {"text": "$detect.objects.upper"}})
    with pytest.raises(PlanError, match="missing"):
        resolve_params(1, p, [{"objects": "abc"}])  # no attribute access on a string
