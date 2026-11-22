"""CameraView: the head camera's range and field of view, shared by FakePerceiver and SimPerceiver (ADR-0024)."""

import math

from spingi.core.types import Pose2D
from spingi.perception.view import HEAD_CAMERA, CameraView


def test_the_head_camera_reads_objects_within_3_m_and_40_degrees_of_the_heading():
    assert (HEAD_CAMERA.max_range_m, HEAD_CAMERA.half_fov_rad) == (3.0, math.radians(40.0))
    robot = Pose2D(x=0.0, y=0.0, yaw=0.0)
    assert HEAD_CAMERA.sees(robot, 2.9, 0.0)
    assert not HEAD_CAMERA.sees(robot, 3.1, 0.0)  # too far
    assert HEAD_CAMERA.sees(robot, 2.0 * math.cos(math.radians(39)), 2.0 * math.sin(math.radians(39)))
    assert not HEAD_CAMERA.sees(robot, 2.0 * math.cos(math.radians(41)), 2.0 * math.sin(math.radians(41)))
    assert not HEAD_CAMERA.sees(robot, -1.0, 0.0)  # behind


def test_the_view_turns_with_the_robot_across_the_back():
    facing_back = Pose2D(x=1.0, y=1.0, yaw=math.pi - 0.1)  # bearings near +-pi must wrap
    assert HEAD_CAMERA.sees(facing_back, -1.0, 1.0 - 0.3)  # just past -pi from the robot
    assert not HEAD_CAMERA.sees(facing_back, 3.0, 1.0)


def test_a_longer_range_can_be_given_for_a_single_look():
    robot = Pose2D(x=0.0, y=0.0, yaw=math.pi / 2)
    assert not HEAD_CAMERA.sees(robot, 0.0, 3.5)
    assert HEAD_CAMERA.sees(robot, 0.0, 3.5, max_range_m=4.0)  # a landmark's tag, read from farther
    assert not CameraView(half_fov_rad=math.radians(10)).sees(robot, 0.5, 2.0)
