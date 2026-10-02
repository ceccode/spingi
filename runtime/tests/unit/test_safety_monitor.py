import asyncio

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.types import Pose2D
from spingi.safety import Geofence, SafetyLimits, SafetyMonitor


def make(limits: SafetyLimits, **kw):
    adapter = FakeAdapter(**kw)
    log = EventLog(run_id="r-safety")
    return SafetyMonitor(adapter, log, limits, period_s=0.0), adapter, log


async def test_geofence_violation_stops_the_robot_and_logs():
    monitor, adapter, log = make(SafetyLimits(geofence=Geofence(x_min=-1, x_max=1, y_min=-1, y_max=1)))
    adapter.pose = Pose2D(x=5.0, y=0.0)
    await monitor.check_once()
    assert monitor.tripped and adapter.stop_called == 1 and not adapter.estopped
    assert log.count("safety.geofence") == 1


async def test_geofence_can_escalate_to_estop():
    monitor, adapter, log = make(
        SafetyLimits(geofence=Geofence(x_min=-1, x_max=1, y_min=-1, y_max=1), estop_on_geofence=True)
    )
    adapter.pose = Pose2D(x=5.0, y=0.0)
    await monitor.check_once()
    assert adapter.estopped


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
    assert adapter.speed_cap == 0.3 and log.count("safety.speed_capped") == 1
    assert monitor.checks >= 1 and log.count("safety.armed") == 1 and log.count("safety.disarmed") == 1
