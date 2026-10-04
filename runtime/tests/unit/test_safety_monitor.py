import asyncio

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.types import Pose2D
from spingi.safety import Geofence, SafetyLimits, SafetyMonitor


def make(limits: SafetyLimits, **kw):
    adapter = FakeAdapter(**kw)
    log = EventLog(run_id="r-safety")
    return SafetyMonitor(adapter, log, limits, period_s=0.001), adapter, log


async def test_leaving_the_geofence_estops_by_default_and_is_recorded_once():
    monitor, adapter, log = make(SafetyLimits(geofence=Geofence(x_min=-1, x_max=1, y_min=-1, y_max=1)))
    adapter.pose = Pose2D(x=5.0, y=0.0)
    for _ in range(5):
        await monitor.check_once()
    assert monitor.tripped and adapter.estopped
    assert log.count("safety.geofence") == 1 and len(monitor.violations) == 1  # latched, not flooding the log


async def test_plain_stop_mode_stops_on_every_check_while_outside():
    fence = Geofence(x_min=-1, x_max=1, y_min=-1, y_max=1)
    monitor, adapter, log = make(SafetyLimits(geofence=fence, estop_on_geofence=False))
    adapter.pose = Pose2D(x=5.0, y=0.0)
    await monitor.check_once()
    await monitor.check_once()
    assert adapter.stop_called == 2 and not adapter.estopped and log.count("safety.geofence") == 1
    adapter.pose = Pose2D(x=0.0, y=0.0)
    await monitor.check_once()
    adapter.pose = Pose2D(x=5.0, y=0.0)
    await monitor.check_once()
    assert log.count("safety.geofence") == 2  # a new occurrence after coming back inside


async def test_low_battery_trips():
    monitor, adapter, log = make(SafetyLimits(min_battery_pct=20), battery_pct=10)
    await monitor.check_once()
    assert log.count("safety.battery_low") == 1 and adapter.stop_called == 1


async def test_inside_limits_nothing_happens_and_heartbeat_is_fed():
    t = [0.0]
    monitor, adapter, log = make(SafetyLimits(geofence=Geofence(x_min=-1, x_max=1, y_min=-1, y_max=1)))
    adapter._clock = lambda: t[0]
    adapter.watchdog_ms = 100
    t[0] = 10.0
    await monitor.check_once()  # heartbeat refreshes the watchdog
    assert not monitor.tripped and adapter.stop_called == 0
    await adapter.walk_to(Pose2D(x=0.5, y=0), max_speed=0.5)
    assert adapter.pose.x == 0.5  # the watchdog did not fire because the monitor sent a heartbeat


async def test_start_applies_speed_cap_and_loop_runs():
    monitor, adapter, log = make(SafetyLimits(max_speed=0.3), speed_cap=1.0)
    await monitor.start()
    await asyncio.sleep(0.01)
    await monitor.stop()
    assert adapter.speed_cap == 0.3 and log.find("safety.speed_capped")[0].data == {"limit": 0.3, "applied": 0.3}
    assert monitor.checks >= 1 and log.count("safety.armed") == 1 and log.count("safety.disarmed") == 1


def test_zero_period_is_refused():
    import pytest

    with pytest.raises(ValueError):
        SafetyMonitor(FakeAdapter(), EventLog(run_id="r"), SafetyLimits(), period_s=0)


async def test_a_crashing_check_estops_the_robot_instead_of_failing_silently():
    import asyncio

    class BrokenPose(FakeAdapter):
        async def get_pose(self):
            raise RuntimeError("pose sensor lost")

    adapter = BrokenPose()
    log = EventLog(run_id="r")
    monitor = SafetyMonitor(adapter, log, SafetyLimits(), period_s=0.01)
    await monitor.start()
    await asyncio.sleep(0.05)
    await monitor.stop()
    assert adapter.estopped and monitor.tripped and "pose sensor lost" in monitor.failed
    assert log.count("safety.monitor_error") == 1
