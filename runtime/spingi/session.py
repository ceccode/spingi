"""One run of a plan in a scene: wires adapter, perceiver, safety monitor, executor and episode writer.

Shared by `spingi run` and `spingi bench` so that a benchmark run is exactly a normal run.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.plan import TaskPlan, load_plan
from spingi.core.ports import HumanGateway
from spingi.episode import RunConfig, write_episode, zip_episode
from spingi.perception.fake import FakePerceiver
from spingi.safety import SafetyMonitor
from spingi.scenes import load_safety_limits, load_world
from spingi.skills import default_registry

AdapterKind = Literal["fake", "sim"]

MONITOR_PERIOD_S = 0.1  # robot time between safety checks (fast simulation yields every 0.2 s)
ESTOP_TIMEOUT_S = 2.0  # wall-clock limit for a stop or e-stop command to return


@dataclass
class SessionConfig:
    plan: Path
    scene: Path
    adapter: AdapterKind = "fake"
    runs_dir: Path = Path("runs")
    perception_noise: float = 0.0  # false-negative rate of the perceiver
    position_sigma_m: float = 0.0  # gaussian noise on perceived positions
    seed: int = 0
    realtime: bool = False
    view: bool = False
    record_video: bool = False
    write_episode: bool = True
    zip_episode: bool = False
    # Safety layer S1: the adapter stops the robot if the SafetyMonitor's heartbeat is older than this, in robot time.
    watchdog_ms: int = 500


@dataclass
class SessionResult:
    run_id: str
    run_dir: Path
    status: str
    steps_completed: int
    steps_total: int
    log: EventLog
    video: Path | None = None
    archive: Path | None = None
    sim_time_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


def new_run_id() -> str:
    return f"r-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


async def run_session(cfg: SessionConfig, human: HumanGateway, log: EventLog | None = None) -> SessionResult:
    run_id = log.run_id if log is not None else new_run_id()
    run_dir = cfg.runs_dir / run_id
    log = log or EventLog(run_id=run_id, path=run_dir / "events.jsonl" if cfg.write_episode else None)
    plan: TaskPlan = load_plan(cfg.plan)
    state = load_world(cfg.scene)

    if cfg.adapter == "sim":
        from spingi.adapters.sim_mujoco import SimAdapter
        from spingi.perception.sim import SimPerceiver

        realtime = cfg.realtime or cfg.view
        robot = SimAdapter(
            cfg.scene,
            realtime=realtime,
            viewer=cfg.view,
            record_dir=run_dir if cfg.write_episode else None,
            record_video=cfg.record_video,
            watchdog_ms=cfg.watchdog_ms,
        )
        perceiver = SimPerceiver(
            robot, false_negative_rate=cfg.perception_noise, position_sigma_m=cfg.position_sigma_m, seed=cfg.seed
        )
    else:
        robot = FakeAdapter(start=state.robot.pose, watchdog_ms=cfg.watchdog_ms)
        perceiver = FakePerceiver(
            objects=list(state.objects.values()), false_negative_rate=cfg.perception_noise, seed=cfg.seed
        )

    executor = Executor(
        registry=default_registry(),
        robot=robot,
        perceiver=perceiver,
        human=human,
        log=log,
        clock=robot.clock.now,
        deadline_clock=robot.clock,
    )
    monitor = SafetyMonitor(robot, log, load_safety_limits(cfg.scene), period_s=MONITOR_PERIOD_S)

    loop = asyncio.get_running_loop()
    await monitor.start()  # armed (speed cap, heartbeat) before the first motion command can be issued
    run_task = asyncio.create_task(executor.run(plan, state))
    estop_task: asyncio.Task | None = None
    handler_installed = False

    def operator_stop() -> None:
        """Ctrl+C is the operator's stop button: e-stop the robot and end the run. A second Ctrl+C kills the
        process (the default handler is restored), so a hung e-stop can never trap the operator."""
        nonlocal estop_task, handler_installed
        if estop_task is None:
            estop_task = loop.create_task(asyncio.wait_for(robot.estop(), ESTOP_TIMEOUT_S))
            run_task.cancel()
            with contextlib.suppress(Exception):
                log.emit("operator.stop", reason="interrupt from the console")
        loop.remove_signal_handler(signal.SIGINT)
        handler_installed = False
        signal.signal(signal.SIGINT, signal.default_int_handler)

    try:
        loop.add_signal_handler(signal.SIGINT, operator_stop)
        handler_installed = True
    except (NotImplementedError, RuntimeError):  # Windows, or not in the main thread
        pass

    status, steps_completed = "aborted", 0
    video = None
    try:
        result = await run_task
        status, steps_completed = result.status, result.steps_completed
    except asyncio.CancelledError:
        steps_completed = log.count("step.end")
        log.emit("run.end", status="aborted", steps_completed=steps_completed, reason="operator stop")
    except Exception as exc:  # noqa: BLE001 - whatever failed, the robot must be stopped and the run recorded
        status, steps_completed = "error", log.count("step.end")
        with contextlib.suppress(Exception):
            log.emit("run.end", status="error", steps_completed=steps_completed, reason=repr(exc))
    finally:
        # Each cleanup step is independent: one failing must not skip the others, and stopping comes first.
        if estop_task is not None:
            with contextlib.suppress(Exception):
                await estop_task
        if status != "success" and not robot.estopped:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(robot.stop(), ESTOP_TIMEOUT_S)
        if handler_installed:
            loop.remove_signal_handler(signal.SIGINT)
        with contextlib.suppress(Exception):
            await monitor.stop()
        if hasattr(robot, "close"):
            with contextlib.suppress(Exception):
                video = robot.close()

    archive = None
    if cfg.write_episode:
        write_episode(
            run_dir,
            log=log,
            plan_path=cfg.plan,
            scene_path=cfg.scene,
            plan_id=plan.id,
            steps_total=len(plan.steps),
            status=status,
            steps_completed=steps_completed,
            adapter=robot,
            adapter_name="sim_mujoco" if cfg.adapter == "sim" else "fake",
            robot_model="unitree_g1" if cfg.adapter == "sim" else "fake",
            config=RunConfig(
                perception_noise=cfg.perception_noise, position_sigma_m=cfg.position_sigma_m, seed=cfg.seed
            ),
        )
        archive = zip_episode(run_dir) if cfg.zip_episode else None

    return SessionResult(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        steps_completed=steps_completed,
        steps_total=len(plan.steps),
        log=log,
        video=video,
        archive=archive,
        sim_time_s=getattr(robot, "sim_time_s", None),
    )
