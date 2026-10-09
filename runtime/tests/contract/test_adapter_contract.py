"""The same suite for every RobotAdapter and every robot profile: FakeAdapter, SimAdapter with the G1 and with the
Go2 today, the real robots' adapters from M4."""

from pathlib import Path

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.ports import ALL_CAPABILITIES, CapabilityMissing, Frame, RobotAdapter
from spingi.core.types import Pose2D, Pose3D
from spingi.robots import GO2

ADAPTERS = {
    "fake": lambda: FakeAdapter(speed_cap=0.6),
    "fake-go2": lambda: FakeAdapter(speed_cap=0.6, capabilities=GO2.capabilities),
}

try:
    import mujoco  # noqa: F401

    if Path("sim/models/unitree_g1/g1.xml").exists():
        from spingi.adapters.sim_mujoco import SimAdapter

        ADAPTERS["sim"] = lambda: SimAdapter("sim/scenes/lab_small.yaml", speed_cap=0.6)
        ADAPTERS["sim-go2"] = lambda: SimAdapter("sim/scenes/lab_small.yaml", robot="go2", speed_cap=0.6)
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


def test_capabilities_are_declared(adapter):
    assert adapter.capabilities and adapter.capabilities <= ALL_CAPABILITIES


async def test_gripper_reports_what_it_holds(adapter):
    if "arm" not in adapter.capabilities:
        pytest.skip("no arm")
    opened = await adapter.gripper("right", "open")
    assert opened.holding is False


async def test_a_missing_capability_raises_instead_of_pretending(adapter):
    if "arm" in adapter.capabilities:
        pytest.skip("has an arm")
    with pytest.raises(CapabilityMissing):
        await adapter.gripper("right", "close")
    with pytest.raises(CapabilityMissing):
        await adapter.move_arm("right", Pose3D(x=0.3, y=0.0, z=0.3), duration_s=0.1)


def test_clock_is_monotonic(adapter):
    a = adapter.clock.now()
    assert adapter.clock.now() >= a
