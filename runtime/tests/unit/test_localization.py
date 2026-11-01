"""Localization with landmarks (ADR-0016): the geometry of a correction, and how navigate uses it on a robot whose
pose estimate drifts. The robot here is a fake with a known error, so every number can be checked by hand."""

import math
import random

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.adapters.sim_mujoco.odometry import as_pose, compose, inverse, planar
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.localization import facing, in_view, pose_from_landmark, wrap, yaw_of
from spingi.core.plan import Step, TaskPlan
from spingi.core.skill import SkillContext
from spingi.core.types import Landmark, Location, Pose2D, Pose3D, RobotState, WorldState
from spingi.skills import default_registry
from spingi.skills.navigate import NavigateParams, NavigateSkill


def seen_from(truth: Pose2D, believed: Pose2D, landmark: Landmark) -> Pose3D:
    """Where a robot that believes it is at `believed`, while it is at `truth`, sees the landmark."""
    offset = compose(planar(believed), inverse(planar(truth)))
    x, y, yaw = compose(offset, planar(landmark.pose))
    return facing(x, y, landmark.height_m, yaw)


def test_a_landmark_seen_from_a_drifted_estimate_gives_the_real_pose():
    rng = random.Random(7)
    for _ in range(50):
        lm = Landmark(marker_id=40, pose=Pose2D(x=rng.uniform(-5, 5), y=rng.uniform(-5, 5), yaw=rng.uniform(-3, 3)))
        truth = Pose2D(x=rng.uniform(-5, 5), y=rng.uniform(-5, 5), yaw=rng.uniform(-3, 3))
        believed = Pose2D(
            x=truth.x + rng.gauss(0, 0.5), y=truth.y + rng.gauss(0, 0.5), yaw=truth.yaw + rng.gauss(0, 0.2)
        )
        fixed = pose_from_landmark(believed, seen_from(truth, believed, lm), lm)
        assert fixed.distance_to(truth) < 1e-9 and abs(wrap(fixed.yaw - truth.yaw)) < 1e-9


def test_a_marker_pose_carries_the_direction_its_face_looks():
    for yaw in (-3.0, -1.2, 0.0, 0.7, 3.1):
        assert yaw_of(facing(1.0, 2.0, 0.5, yaw)) == pytest.approx(yaw)


def test_a_landmark_is_in_view_only_in_front_near_enough_and_facing_the_camera():
    lm = Landmark(marker_id=1, pose=Pose2D(x=3.0, y=0.0, yaw=math.pi))  # faces the origin
    here = Pose2D(x=0.0, y=0.0, yaw=0.0)
    assert in_view(here, lm, 4.0, math.radians(45))
    assert not in_view(here, lm, 2.0, math.radians(45))  # too far
    assert not in_view(Pose2D(x=0.0, y=0.0, yaw=math.pi), lm, 4.0, math.radians(45))  # behind the camera
    back = Landmark(marker_id=2, pose=Pose2D(x=3.0, y=0.0, yaw=0.0))  # turned away from the robot
    assert not in_view(here, back, 4.0, math.radians(45))


class DriftingRobot(FakeAdapter):
    """A fake whose walks end off target by a fixed error, believing they did not: odometry drift, made exact.
    `seen` is what its camera sees: a landmark placed in its believed frame."""

    def __init__(self, errors: list[tuple[float, float, float]], **kw) -> None:
        super().__init__(**kw)
        self.truth = self.pose
        self.errors = list(errors)  # (dx, dy, dyaw) added to each walk, in order; none left: exact walks
        self.relocalized: list[Pose2D] = []

    @property
    def offset(self):
        return compose(planar(self.pose), inverse(planar(self.truth)))

    async def walk_to(self, pose, max_speed):
        true_target = as_pose(compose(inverse(self.offset), planar(pose)))
        await super().walk_to(pose, max_speed)  # believes it arrived
        dx, dy, dyaw = self.errors.pop(0) if self.errors else (0.0, 0.0, 0.0)
        self.truth = Pose2D(x=true_target.x + dx, y=true_target.y + dy, yaw=true_target.yaw + dyaw)

    async def relocalize(self, pose):
        self.relocalized.append(pose)
        self.pose = pose

    def seen(self, landmark: Landmark) -> Pose3D | None:
        if not in_view(self.truth, landmark, 4.0, math.radians(45)):
            return None
        return seen_from(self.truth, self.pose, landmark)


class LandmarkPerceiver:
    def __init__(self, robot: DriftingRobot, landmarks: list[Landmark], misread: float = 0.0) -> None:
        self.robot, self.landmarks, self.misread = robot, {lm.marker_id: lm for lm in landmarks}, misread

    async def detect(self, frame, cls):
        return []

    async def localize(self, frame, marker_id):
        lm = self.landmarks.get(marker_id)
        seen = self.robot.seen(lm) if lm else None
        if seen is not None and self.misread:
            return seen.model_copy(update={"x": seen.x + self.misread})
        return seen


LANDMARK = Landmark(marker_id=40, pose=Pose2D(x=7.5, y=1.0, yaw=math.pi - 0.4), size_m=0.3)


def world(landmarks=(LANDMARK,)) -> WorldState:
    return WorldState(
        locations={
            "dock": Location(name="dock", pose=Pose2D(x=0.0, y=0.0)),
            "gate": Location(name="gate", pose=Pose2D(x=3.0, y=0.0)),
            "pump": Location(name="pump", pose=Pose2D(x=5.0, y=0.0)),
        },
        landmarks=list(landmarks),
        robot=RobotState(pose=Pose2D(x=0.0, y=0.0)),
    )


async def navigate(robot, perceiver, state, **params):
    log = EventLog(run_id="r-loc")
    ctx = SkillContext(state=state, robot=robot, perceiver=perceiver, log=log, deadline_s=60)
    result = await NavigateSkill().execute(NavigateParams(**params), ctx)
    return result, log


async def test_navigate_corrects_its_pose_at_a_landmark_and_walks_the_difference():
    robot = DriftingRobot(errors=[(0.0, -0.3, 0.05)])  # the walk ends 30 cm right and 3 degrees off, unknown
    result, log = await navigate(robot, LandmarkPerceiver(robot, [LANDMARK]), world(), to="pump")
    target = Pose2D(x=5.0, y=0.0)
    assert result.outcome == "success"
    fixes = log.find("robot.relocalized")
    assert len(fixes) == 2 and fixes[0].data["correction_m"] == pytest.approx(0.3, abs=0.001)
    assert fixes[0].data["correction_rad"] == pytest.approx(0.05, abs=0.001) and fixes[1].data["correction_m"] < 1e-3
    assert robot.truth.distance_to(target) < 1e-6  # really there, not just believing it
    assert len(log.find("adapter.call")) == 2  # the leg, then the walk back after the correction


async def test_without_landmarks_the_robot_believes_it_arrived_where_it_is_not():
    robot = DriftingRobot(errors=[(0.0, -0.3, 0.05)])
    result, log = await navigate(robot, LandmarkPerceiver(robot, []), world(landmarks=()), to="pump")
    assert result.outcome == "success" and (await robot.get_pose()).distance_to(Pose2D(x=5.0, y=0.0)) < 1e-6
    assert robot.truth.distance_to(Pose2D(x=5.0, y=0.0)) == pytest.approx(0.3)
    assert not log.find("robot.relocalized") and not log.find("localization.missed")


async def test_a_landmark_expected_and_not_seen_is_logged():
    robot = DriftingRobot(errors=[])
    perceiver = LandmarkPerceiver(robot, [])  # the tag is on the map but the camera never reads it
    _, log = await navigate(robot, perceiver, world(), to="pump")
    assert [e.data["markers"] for e in log.find("localization.missed")] == [[40]]  # at the pump, not at the dock
    assert not robot.relocalized


async def test_a_misread_tag_far_from_the_estimate_is_refused():
    robot = DriftingRobot(errors=[])
    _, log = await navigate(robot, LandmarkPerceiver(robot, [LANDMARK], misread=3.0), world(), to="pump")
    assert log.find("localization.rejected") and not robot.relocalized


async def test_a_retried_route_resumes_from_the_waypoint_the_robot_stands_at():
    robot = DriftingRobot(errors=[], start=Pose2D(x=3.0, y=0.0))  # already at the gate
    _, log = await navigate(robot, LandmarkPerceiver(robot, []), world(landmarks=()), to="pump", via=["dock", "gate"])
    assert [e.data["to"] for e in log.find("adapter.call")] == ["pump"]


async def test_a_plan_records_every_correction():
    robot = DriftingRobot(errors=[(0.2, 0.1, -0.04)])
    log = EventLog(run_id="r-loc-plan")
    executor = Executor(
        default_registry(), robot, LandmarkPerceiver(robot, [LANDMARK]), ScriptedHuman(default="abort"), log=log
    )
    result = await executor.run(TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "pump"})]), world())
    assert result.ok and log.count("robot.relocalized") >= 1
    assert result.final_state.robot.pose.distance_to(robot.truth) < 1e-6
