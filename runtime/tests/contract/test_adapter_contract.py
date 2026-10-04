"""The same suite for every RobotAdapter: FakeAdapter and SimAdapter today, the real G1 adapter from M4."""

from pathlib import Path

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.ports import Frame, RobotAdapter
from spingi.core.types import Pose2D

ADAPTERS = {
    "fake": lambda: FakeAdapter(speed_cap=0.6),
}

try:
    import mujoco  # noqa: F401

    if Path("sim/models/unitree_g1/g1.xml").exists():
        from spingi.adapters.sim_mujoco import SimAdapter

        ADAPTERS["sim"] = lambda: SimAdapter("sim/scenes/lab_small.yaml", speed_cap=0.6)
except ImportError:
    pass


@pytest.fixture(params=list(ADAPTERS))
def adapter(request):
    return ADAPTERS[request.param]()


def test_implements_protocol(adapter):
    assert isinstance(adapter, RobotAdapter)


async def test_walk_to_reaches_pose_within_tolerance(adapter):
    target = Pose2D(x=1.0, y=0.5, yaw=0.0)
    await adapter.walk_to(target, max_speed=0.3)
    assert (await adapter.get_pose()).distance_to(target) <= 0.15


async def test_requested_speed_above_cap_is_clamped(adapter):
    await adapter.walk_to(Pose2D(x=0.5, y=0), max_speed=5.0)
    assert adapter.last_applied_speed is not None and adapter.last_applied_speed <= 0.6


async def test_stop_is_idempotent(adapter):
    await adapter.stop()
    await adapter.stop()


async def test_camera_returns_a_frame(adapter):
    frame = await adapter.get_camera()
    assert isinstance(frame, Frame) and frame.id


async def test_battery_in_range(adapter):
    assert 0.0 <= await adapter.get_battery() <= 100.0


async def test_estop_never_raises(adapter):
    await adapter.estop()


async def test_speed_limit_never_goes_up(adapter):
    assert adapter.set_speed_limit(0.3) == 0.3
    assert adapter.set_speed_limit(5.0) == 0.3
    await adapter.walk_to(Pose2D(x=0.3, y=0), max_speed=2.0)
    assert adapter.last_applied_speed <= 0.3


async def test_gripper_reports_what_it_holds(adapter):
    opened = await adapter.gripper("right", "open")
    assert opened.holding is False


def test_clock_is_monotonic(adapter):
    a = adapter.clock.now()
    assert adapter.clock.now() >= a
