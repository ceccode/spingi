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
from spingi.core.ports import HumanGateway, Perceiver, RobotAdapter
from spingi.core.types import WorldState
from spingi.episode import (
    LocomotionKind,
    OdometryKind,
    PerceptionKind,
    RobotInfo,
    RunConfig,
    RunOutcome,
    write_episode,
    zip_episode,
)
from spingi.perception.fake import FakePerceiver
from spingi.robots import DEFAULT_ROBOT, RobotProfile, get_profile
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
    robot: str = DEFAULT_ROBOT  # a profile alias from spingi.robots: which robot the adapter stands for
    # truth: the perceiver reads the scene's ground truth (with --noise/--sigma); markers: it reads AprilTags from
    # the rendered frames, like it will from a real camera (sim adapter only; noise settings do not apply).
    perception: PerceptionKind = "truth"
    # kinematic: the base glides along the path (fast, exactly repeatable); physics: the robot walks with a learned
    # controller on the scene's ground and can be blocked or fall (sim adapter only, ADR-0015).
    locomotion: LocomotionKind = "kinematic"
    # truth: the robot knows exactly where it is; drift: its pose estimate drifts like a real robot's odometry and
    # only landmarks correct it (physics locomotion only, ADR-0016).
    odometry: OdometryKind = "truth"
    camera_noise: bool = False  # blurred, noisy, JPEG-compressed head-camera frames (sim adapter, ADR-0016)
    runs_dir: Path = Path("runs")
    perception_noise: float = 0.0  # false-negative rate of the perceiver
    position_sigma_m: float = 0.0  # gaussian noise on perceived positions
    seed: int = 0
    realtime: bool = False
    view: bool = False
    record_video: bool = False  # third-person video, runs/<id>/run.mp4 (sim adapter)
    record_camera: bool = False  # the head camera's view at 10 frames per second, runs/<id>/head.mp4 (sim adapter)
    write_episode: bool = True
    zip_episode: bool = False
    # Safety layer S1: the adapter stops the robot if the SafetyMonitor's heartbeat is older than this, in robot time.
    watchdog_ms: int = 500

    def __post_init__(self) -> None:
        """Settings that contradict each other are refused here, before anything is loaded."""
        if self.adapter != "sim":
            if self.perception == "markers":
                raise ValueError("perception 'markers' reads camera images: it needs the sim adapter")
            if self.locomotion == "physics":
                raise ValueError("locomotion 'physics' needs the sim adapter")
            if self.odometry == "drift" or self.camera_noise:
                raise ValueError("odometry drift and camera noise are simulated sensors: they need the sim adapter")
            if self.record_video or self.record_camera:
                raise ValueError("videos are rendered by the simulator: they need the sim adapter")
        if self.odometry == "drift" and self.locomotion != "physics":
            raise ValueError("odometry drift needs physics locomotion: the kinematic base moves exactly")

    def run_config(self) -> RunConfig:
        """The settings an episode keeps, to be run again (spingi replay)."""
        return RunConfig(
            perception_noise=self.perception_noise,
            position_sigma_m=self.position_sigma_m,
            seed=self.seed,
            perception=self.perception,
            locomotion=self.locomotion,
            odometry=self.odometry,
            camera_noise=self.camera_noise,
        )


@dataclass
class SessionResult:
    run_id: str
    run_dir: Path
    status: str
    steps_completed: int
    steps_total: int
    log: EventLog
    video: Path | None = None
    camera_video: Path | None = None
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
    profile = get_profile(cfg.robot)
    robot, perceiver = _robot_and_perceiver(cfg, profile, state, run_dir, log)

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
    await monitor.start()  # armed (speed cap, heartbeat) before the first motion command can be issued
    run_task = asyncio.create_task(executor.run(plan, state))
    stop_button = StopButton(robot, run_task, log)
    stop_button.install()

    status, steps_completed = "aborted", 0
    try:
        status, steps_completed = await _outcome(run_task, log)
    finally:
        await _shut_down(robot, monitor, stop_button, success=status == "success")

    recording = robot.recording()
    outcome = RunOutcome(plan_id=plan.id, status=status, steps_completed=steps_completed, steps_total=len(plan.steps))
    archive = None
    if cfg.write_episode:
        write_episode(
            run_dir,
            log=log,
            plan_path=cfg.plan,
            scene_path=cfg.scene,
            outcome=outcome,
            robot=RobotInfo(model=profile.name, adapter="sim_mujoco" if cfg.adapter == "sim" else "fake"),
            recording=recording,
            config=cfg.run_config(),
        )
        archive = zip_episode(run_dir) if cfg.zip_episode else None

    camera = recording.camera_video
    return SessionResult(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        steps_completed=steps_completed,
        steps_total=len(plan.steps),
        log=log,
        video=recording.video,
        camera_video=run_dir / camera["file"] if camera else None,
        archive=archive,
        sim_time_s=recording.sim_time_s,
    )


def _robot_and_perceiver(
    cfg: SessionConfig, profile: RobotProfile, state: WorldState, run_dir: Path, log: EventLog
) -> tuple[RobotAdapter, Perceiver]:
    """The robot the session stands for and what reads its camera."""
    if cfg.adapter == "fake":
        robot = FakeAdapter(start=state.robot.pose, watchdog_ms=cfg.watchdog_ms, capabilities=profile.capabilities)
        objects = list(state.objects.values())
        return robot, FakePerceiver(objects=objects, false_negative_rate=cfg.perception_noise, seed=cfg.seed)

    from spingi.adapters.sim_mujoco import SimAdapter

    sim = SimAdapter(
        cfg.scene,
        robot=profile,
        realtime=cfg.realtime or cfg.view,
        viewer=cfg.view,
        record_dir=run_dir if cfg.write_episode else None,
        record_video=cfg.record_video,
        record_camera=cfg.record_camera,
        watchdog_ms=cfg.watchdog_ms,
        locomotion=cfg.locomotion,
        start=state.robot.pose,
        seed=cfg.seed,
        odometry=cfg.odometry,
        camera_noise=cfg.camera_noise,
        log=log,
    )
    if cfg.perception == "markers":
        from spingi.perception.markers import MarkerPerceiver

        return sim, MarkerPerceiver.for_adapter(sim, cfg.scene)
    from spingi.perception.sim import SimPerceiver

    noise = {"false_negative_rate": cfg.perception_noise, "position_sigma_m": cfg.position_sigma_m}
    return sim, SimPerceiver(sim, seed=cfg.seed, **noise)


class StopButton:
    """Ctrl+C is the operator's stop button: it e-stops the robot and ends the run. A second Ctrl+C kills the
    process (the default handler is restored), so a hung e-stop can never trap the operator."""

    def __init__(self, robot: RobotAdapter, run_task: asyncio.Task, log: EventLog) -> None:
        self._robot = robot
        self._run_task = run_task
        self._log = log
        self._loop = asyncio.get_running_loop()
        self._estop: asyncio.Task | None = None
        self._installed = False

    def install(self) -> None:
        try:
            self._loop.add_signal_handler(signal.SIGINT, self._pressed)
            self._installed = True
        except (NotImplementedError, RuntimeError):  # Windows, or not in the main thread
            pass

    def release(self) -> None:
        if self._installed:
            self._loop.remove_signal_handler(signal.SIGINT)
            self._installed = False

    async def wait(self) -> None:
        """Until the e-stop it sent, if any, has returned or failed."""
        if self._estop is not None:
            with contextlib.suppress(Exception):
                await self._estop

    def _pressed(self) -> None:
        if self._estop is None:
            self._estop = self._loop.create_task(asyncio.wait_for(self._robot.estop(), ESTOP_TIMEOUT_S))
            self._run_task.cancel()
            with contextlib.suppress(Exception):
                self._log.emit("operator.stop", reason="interrupt from the console")
        self.release()
        signal.signal(signal.SIGINT, signal.default_int_handler)


async def _outcome(run_task: asyncio.Task, log: EventLog) -> tuple[str, int]:
    """The run's status and completed steps. A run stopped by the operator or ended by an unexpected error is
    recorded as such: whatever happened, the run ends in the log."""
    try:
        result = await run_task
        return result.status, result.steps_completed
    except asyncio.CancelledError:
        steps_completed = log.count("step.end")
        log.emit("run.end", status="aborted", steps_completed=steps_completed, reason="operator stop")
        return "aborted", steps_completed
    except Exception as exc:  # noqa: BLE001 - whatever failed, the robot must be stopped and the run recorded
        steps_completed = log.count("step.end")
        with contextlib.suppress(Exception):
            log.emit("run.end", status="error", steps_completed=steps_completed, reason=repr(exc))
        return "error", steps_completed


async def _shut_down(robot: RobotAdapter, monitor: SafetyMonitor, stop_button: StopButton, success: bool) -> None:
    """Each step is independent: one failing must not skip the others, and stopping the robot comes first."""
    await stop_button.wait()
    if not success and not robot.estopped:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(robot.stop(), ESTOP_TIMEOUT_S)
    stop_button.release()
    with contextlib.suppress(Exception):
        await monitor.stop()
    with contextlib.suppress(Exception):
        robot.close()
