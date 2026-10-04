from spingi.core.plan import Step, TaskPlan
from spingi.core.types import Pose2D, Pose3D, RobotState
from spingi.perception.fake import FakePerceiver
from spingi.skills.pick import PickParams, PickSkill
from spingi.skills.place import PlaceParams, PlaceSkill
from tests.conftest import red_box, world


def test_pick_requires_known_pose_within_reach_and_free_hand():
    skill = PickSkill()
    assert not skill.preconditions(PickParams(object_id="red_box_01"), world()).ok  # unknown object
    far = world(objects={"red_box_01": red_box()})  # box at (0, 2.4), robot at (0, 0): 2.4 m away
    assert not skill.preconditions(PickParams(object_id="red_box_01"), far).ok
    near = world(objects={"red_box_01": red_box()}, robot=RobotState(pose=Pose2D(x=0, y=2.0, yaw=1.57)))
    assert skill.preconditions(PickParams(object_id="red_box_01"), near).ok
    near.robot.holding = red_box()
    assert not skill.preconditions(PickParams(object_id="red_box_01"), near).ok
    no_pose = world(objects={"red_box_01": red_box(with_pose=False)})
    assert not skill.preconditions(PickParams(object_id="red_box_01"), no_pose).ok


def test_place_requires_holding_and_being_at_location():
    skill = PlaceSkill()
    assert not skill.preconditions(PlaceParams(at="dock"), world()).ok
    holding = world(robot=RobotState(pose=Pose2D(x=0, y=0), holding=red_box()))
    assert skill.preconditions(PlaceParams(at="dock"), holding).ok
    assert not skill.preconditions(PlaceParams(at="shelf_A"), holding).ok


async def test_detect_pick_carry_place_on_fake_adapter(make_executor):
    perceiver = FakePerceiver(objects=[red_box()])
    executor, adapter, log = make_executor(perceiver=perceiver)
    plan = TaskPlan(
        id="runner",
        steps=[
            Step(skill="navigate", params={"to": "shelf_A"}),
            Step(skill="detect", params={"cls": "red_box"}),
            Step(skill="pick", params={"object_id": "$detect.objects[0].id"}),
            Step(skill="navigate", params={"to": "workstation_B"}),
            Step(skill="place", params={"at": "workstation_B"}),
        ],
    )
    result = await executor.run(plan, world())
    assert result.ok and result.steps_completed == 5
    final = result.final_state
    assert final.robot.holding is None
    placed = final.objects["red_box_01"].pose
    assert placed is not None and abs(placed.x - 6.35) < 0.05 and abs(placed.y - 0.5) < 0.05  # 0.35 m ahead of B
    assert [c[0] for c in adapter.calls if c[0] in ("move_arm", "gripper")] == [
        "move_arm",
        "gripper",
        "move_arm",
        "gripper",
    ]
    assert log.count("perception.result") == 1


async def test_detect_fails_recoverably_when_nothing_is_seen(make_executor):
    executor, adapter, log = make_executor(perceiver=FakePerceiver(always_fail=True))
    plan = TaskPlan(
        id="d", steps=[Step(skill="detect", params={"cls": "red_box"}, on_failure={"retry": 1, "then": "abort"})]
    )
    result = await executor.run(plan, world())
    assert result.status == "aborted" and log.count("skill.end", outcome="recoverable") == 2


async def test_navigate_via_waypoints_reports_path(make_executor):
    executor, adapter, log = make_executor()
    plan = TaskPlan(id="v", steps=[Step(skill="navigate", params={"to": "workstation_B", "via": ["shelf_A"]})])
    result = await executor.run(plan, world())
    assert result.ok and result.outputs[0]["path"] == ["shelf_A", "workstation_B"]
    assert [c[1]["pose"]["y"] for c in adapter.calls if c[0] == "walk_to"] == [2.0, 0.5]


def test_navigate_via_requires_known_waypoints(registry):
    from spingi.core.plan import validate_plan

    plan = TaskPlan(id="v", steps=[Step(skill="navigate", params={"to": "dock", "via": ["nowhere"]})])
    assert validate_plan(plan, registry) == []  # statically fine: names are checked at runtime
    check = registry.get("navigate").preconditions(registry.get("navigate").Params(to="dock", via=["nowhere"]), world())
    assert not check.ok and "nowhere" in check.reason


def test_place_pose_helper_types():
    assert Pose3D(x=1, y=2, z=0.9).z == 0.9


async def test_an_empty_grasp_is_recoverable_and_opens_the_hand(make_executor):
    from spingi.adapters.fake import FakeAdapter

    adapter = FakeAdapter(start=Pose2D(x=0, y=2.0, yaw=1.57))
    adapter.grasp_fails = True
    executor, adapter, log = make_executor(adapter=adapter)
    state = world(objects={"red_box_01": red_box()}, robot=RobotState(pose=Pose2D(x=0, y=2.0, yaw=1.57)))
    plan = TaskPlan(
        id="p", steps=[Step(skill="pick", params={"object_id": "red_box_01"}, on_failure={"then": "abort"})]
    )
    result = await executor.run(plan, state)
    assert result.status == "aborted" and result.final_state.robot.holding is None
    assert "nothing in the right hand" in log.find("skill.end", skill="pick")[0].data["reason"]
    assert [c[1]["action"] for c in adapter.calls if c[0] == "gripper"] == ["close", "open"]
