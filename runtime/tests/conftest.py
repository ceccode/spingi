from __future__ import annotations

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.types import Location, ObjectRef, Pose2D, Pose3D, RobotState, WorldState
from spingi.perception.fake import FakePerceiver
from spingi.skills import default_registry


def world(**kw) -> WorldState:
    """Convenience WorldState: three known locations, robot at the dock, optional objects."""
    locations = {
        "dock": Location(name="dock", pose=Pose2D(x=0, y=0, yaw=0)),
        "shelf_A": Location(name="shelf_A", pose=Pose2D(x=0, y=2, yaw=1.57)),
        "workstation_B": Location(name="workstation_B", pose=Pose2D(x=6, y=0.5, yaw=0)),
    }
    objects = kw.pop("objects", {})
    robot = kw.pop("robot", RobotState(pose=Pose2D(x=0, y=0, yaw=0)))
    return WorldState(locations=locations, objects=objects, robot=robot, **kw)


def red_box(with_pose: bool = True) -> ObjectRef:
    return ObjectRef(id="red_box_01", cls="red_box", marker_id=7, pose=Pose3D(x=0, y=2.4, z=0.9) if with_pose else None)


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def make_executor(registry):
    def _make(
        adapter: FakeAdapter | None = None,
        human: ScriptedHuman | None = None,
        perceiver: FakePerceiver | None = None,
        **kw,
    ) -> tuple[Executor, FakeAdapter, EventLog]:
        adapter = adapter or FakeAdapter()
        log = EventLog(run_id="r-test")
        executor = Executor(
            registry=registry,
            robot=adapter,
            perceiver=perceiver or FakePerceiver(),
            human=human or ScriptedHuman(default="abort"),
            log=log,
            **kw,
        )
        return executor, adapter, log

    return _make
