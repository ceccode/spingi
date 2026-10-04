"""Safety and deadlines run on robot time: simulated time in MuJoCo, so a fast simulation is checked as often as
the real robot would be, and nothing spins while the run waits."""

import asyncio
import time
from pathlib import Path

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.events import EventLog
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan
from spingi.core.types import Pose2D
from spingi.safety import SafetyLimits, SafetyMonitor
from spingi.session import SessionConfig, run_session

LAB = Path("sim/scenes/lab_small.yaml")


async def test_watchdog_counts_simulated_time():
    adapter = SimAdapter(LAB, watchdog_ms=100)  # no heartbeat at all
    await adapter.walk_to(Pose2D(x=3.0, y=0.0), max_speed=0.5)
    assert adapter.blocked_by == "watchdog"
    assert 0.1 <= adapter.sim_time_s < 0.2 and adapter.pose.x < 0.1
    adapter.close()


async def test_monitor_heartbeats_keep_a_long_walk_alive():
    adapter = SimAdapter(LAB, watchdog_ms=300)
    monitor = SafetyMonitor(adapter, EventLog(run_id="r"), SafetyLimits(), period_s=0.1)
    await monitor.start()
    await adapter.walk_to(Pose2D(x=3.0, y=0.0), max_speed=0.5)
    await monitor.stop()
    assert adapter.blocked_by is None and abs(adapter.pose.x - 3.0) < 1e-6
    assert monitor.checks >= 25  # one check per 0.2 simulated seconds over a 6 s walk
    adapter.close()


async def test_monitor_does_not_spin_while_the_robot_is_idle():
    adapter = SimAdapter(LAB)
    monitor = SafetyMonitor(adapter, EventLog(run_id="r"), SafetyLimits(), period_s=0.1)
    await monitor.start()
    cpu0, wall0 = time.process_time(), time.monotonic()
    await asyncio.sleep(0.3)  # simulated time does not advance: the monitor must sleep, not poll
    cpu, checks = time.process_time() - cpu0, monitor.checks
    await monitor.stop()
    assert checks == 1 and cpu < 0.1, (checks, cpu, time.monotonic() - wall0)
    adapter.close()


async def test_plan_deadline_is_in_robot_time(tmp_path):
    plan = TaskPlan(
        id="tight",
        deadline_s=5,
        steps=[Step(skill="navigate", params={"to": "workstation_B"}), Step(skill="say", params={"text": "late"})],
    )
    plan_file = tmp_path / "tight.yaml"
    import yaml

    plan_file.write_text(yaml.safe_dump(plan.model_dump()))
    cfg = SessionConfig(plan=plan_file, scene=LAB, adapter="sim", runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman())
    assert result.status == "deadline" and result.steps_completed == 1  # ~12 s of walking > 5 s budget
