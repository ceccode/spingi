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


async def test_watchdog_stops_without_heartbeat():
    t = [0.0]
    adapter = FakeAdapter(watchdog_ms=200, clock=lambda: t[0])
    t[0] = 0.5  # 500 ms without heartbeat
    await adapter.walk_to(Pose2D(x=5, y=0), max_speed=0.5)
    assert adapter.stop_called == 1 and adapter.pose.x == 0  # stopped, it did not move


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
