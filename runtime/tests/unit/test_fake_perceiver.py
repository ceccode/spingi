"""FakePerceiver: every configured object without a robot; with one, only what its head camera has in view."""

import math

from spingi.adapters.fake import FakeAdapter
from spingi.core.ports import Frame
from spingi.core.types import ObjectRef, Pose2D, Pose3D
from spingi.perception.fake import FakePerceiver

FRAME = Frame(id="f", ts=0.0)
AHEAD = ObjectRef(id="box_ahead", cls="box", marker_id=3, pose=Pose3D(x=2.0, y=0.0, z=0.9))
BEHIND = ObjectRef(id="box_behind", cls="box", marker_id=4, pose=Pose3D(x=-5.0, y=0.0, z=0.9))
UNPLACED = ObjectRef(id="box_unplaced", cls="box")


async def test_without_a_robot_it_sees_every_configured_object():
    perceiver = FakePerceiver(objects=[AHEAD, BEHIND, UNPLACED])
    assert [o.id for o in await perceiver.detect(FRAME, "box")] == ["box_ahead", "box_behind", "box_unplaced"]
    assert await perceiver.localize(FRAME, 4) == BEHIND.pose
    assert await perceiver.detect(FRAME, "toy") == []


async def test_with_a_robot_it_sees_what_the_head_camera_has_in_view():
    robot = FakeAdapter(start=Pose2D(x=0.0, y=0.0, yaw=0.0))
    perceiver = FakePerceiver(objects=[AHEAD, BEHIND, UNPLACED], robot=robot)
    assert [o.id for o in await perceiver.detect(FRAME, "box")] == ["box_ahead"]
    assert await perceiver.localize(FRAME, 3) == AHEAD.pose
    assert await perceiver.localize(FRAME, 4) is None


async def test_the_view_follows_the_robot_as_it_walks():
    robot = FakeAdapter(start=Pose2D(x=0.0, y=0.0, yaw=0.0))
    perceiver = FakePerceiver(objects=[AHEAD, BEHIND], robot=robot)
    await robot.walk_to(Pose2D(x=-3.0, y=0.0, yaw=math.pi), max_speed=0.5)
    assert [o.id for o in await perceiver.detect(FRAME, "box")] == ["box_behind"]


async def test_false_negatives_apply_with_a_robot_too():
    robot = FakeAdapter(start=Pose2D(x=0.0, y=0.0, yaw=0.0))
    assert await FakePerceiver(objects=[AHEAD], robot=robot, always_fail=True).detect(FRAME, "box") == []
    noisy = FakePerceiver(objects=[AHEAD], robot=robot, false_negative_rate=0.5, seed=3)
    seen = [len(await noisy.detect(FRAME, "box")) for _ in range(40)]
    assert 0 < sum(seen) < 40 and noisy.detect_calls == 40
