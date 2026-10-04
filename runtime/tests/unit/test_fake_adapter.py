import pytest

from spingi.adapters.fake import EstopEngaged, FakeAdapter
from spingi.core.types import Pose2D


async def test_speed_is_clamped_to_cap():
    adapter = FakeAdapter(speed_cap=0.5)
    await adapter.walk_to(Pose2D(x=1, y=0), max_speed=2.0)
    assert adapter.last_applied_speed == 0.5


async def test_estop_blocks_motion_until_manual_reset():
    adapter = FakeAdapter()
    await adapter.estop()
    with pytest.raises(EstopEngaged):
        await adapter.walk_to(Pose2D(x=1, y=0), max_speed=0.5)
    adapter.reset_estop()
    await adapter.walk_to(Pose2D(x=1, y=0), max_speed=0.5)
    assert adapter.pose.x == 1


async def test_watchdog_refuses_any_motion_without_heartbeat():
    from spingi.adapters.fake import WatchdogExpired
    from spingi.core.types import Pose3D

    t = [0.0]
    adapter = FakeAdapter(watchdog_ms=200, clock=lambda: t[0])
    t[0] = 0.5  # 500 ms without heartbeat
    with pytest.raises(WatchdogExpired):
        await adapter.walk_to(Pose2D(x=5, y=0), max_speed=0.5)
    with pytest.raises(WatchdogExpired):
        await adapter.move_arm("right", Pose3D(x=0, y=0, z=1), duration_s=1)
    with pytest.raises(WatchdogExpired):
        await adapter.gripper("right", "close")
    assert adapter.pose.x == 0 and adapter.blocked_by == "watchdog"


async def test_heartbeat_keeps_robot_alive():
    t = [0.0]
    adapter = FakeAdapter(watchdog_ms=200, clock=lambda: t[0])
    t[0] = 0.5
    await adapter.heartbeat()
    await adapter.walk_to(Pose2D(x=5, y=0), max_speed=0.5)
    assert adapter.pose.x == 5


async def test_battery_drains_with_distance():
    adapter = FakeAdapter(battery_pct=100, battery_drain_per_m=2.0)
    await adapter.walk_to(Pose2D(x=10, y=0), max_speed=0.5)
    assert adapter.battery_pct == 80
