from spingi.core.plan import Step, TaskPlan
from spingi.core.types import Pose2D, RobotState
from spingi.perception.fake import FakePerceiver
from spingi.skills.inspect import InspectParams, InspectSkill
from tests.conftest import red_box, world


def test_inspect_precondition_requires_being_at_target():
    skill = InspectSkill()
    assert not skill.preconditions(InspectParams(target="shelf_A"), world()).ok
    at_shelf = world(robot=RobotState(pose=Pose2D(x=0.05, y=2.0, yaw=1.57)))
    assert skill.preconditions(InspectParams(target="shelf_A"), at_shelf).ok
    assert not skill.preconditions(InspectParams(target="nowhere"), at_shelf).ok


async def test_inspect_evaluates_present_and_absent_checks(make_executor):
    perceiver = FakePerceiver(objects=[red_box()])
    executor, adapter, log = make_executor(perceiver=perceiver)
    state = world(robot=RobotState(pose=Pose2D(x=0.0, y=2.0, yaw=1.57)))
    plan = TaskPlan(
        id="i",
        steps=[
            Step(
                skill="inspect",
                params={"target": "shelf_A", "checks": ["present:red_box", "absent:red_box", "lights on"]},
            )
        ],
    )
    result = await executor.run(plan, state)
    assert result.ok
    evidence = result.outputs[0]
    assert evidence["checks"]["present:red_box"]["passed"] is True
    assert evidence["checks"]["absent:red_box"]["passed"] is False
    assert evidence["checks"]["lights on"]["passed"] is None
    assert evidence["anomalies"] == ["absent:red_box"] and evidence["passed"] is False
    assert log.count("perception.result") == 1 and log.find("perception.result")[0].data["frame_id"]
    assert "red_box_01" in result.final_state.objects  # detections update the world state


async def test_inspect_without_checks_just_records_a_frame(make_executor):
    executor, adapter, log = make_executor()
    state = world(robot=RobotState(pose=Pose2D(x=0.0, y=0.0)))
    result = await executor.run(TaskPlan(id="i", steps=[Step(skill="inspect", params={"target": "dock"})]), state)
    assert result.ok and result.outputs[0]["passed"] is True and result.outputs[0]["checks"] == {}


async def test_inspect_reference_from_navigate_evidence(make_executor):
    executor, adapter, log = make_executor()
    plan = TaskPlan(
        id="chain",
        steps=[
            Step(skill="navigate", params={"to": "shelf_A"}),
            Step(skill="inspect", params={"target": "$navigate.reached"}),
        ],
    )
    result = await executor.run(plan, world())
    assert result.ok and result.outputs[1]["target"] == "shelf_A"
