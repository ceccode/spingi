"""SimAdapter: a robot in MuJoCo (the Unitree G1 by default, or the Go2), moved in one of two ways.

- `kinematic` (ADR-0006, the default): the base is placed along the path at the requested speed, following the
  ground's height; the legs stay in the standing pose; touching an obstacle stops it where it is. Fast and exactly
  repeatable: bench runs and CI.
- `physics` (ADR-0015): the robot walks. A learned walking controller moves the legs, the feet push on the ground
  (asphalt, gravel, grass and their grip), the pose is whatever the physics gives, an obstacle is a real contact and
  the robot can fall. `walk_to` turns the target pose into velocity commands, as it will on the real robot.

Both: battery, e-stop, watchdog, rendered head camera, and two videos: third person (`record_video`) and the head
camera's view, continuously (`record_camera`). Not simulated: arm dynamics (an object
is carried kinematically, ADR-0007). Which model is loaded and how it is moved comes from a `RobotProfile` (ADR-0012).

What the robot knows (ADR-0016): `pose` is the truth, which the simulator uses; `get_pose` returns the robot's
estimate. With `odometry="drift"` (physics only) the estimate drifts as a real robot's odometry does, starting from
where the robot was told it starts; `relocalize` corrects it. With `camera_noise` the head camera's frames are blurred,
noisy and JPEG-compressed, and the robot's belief about its camera's focal length is slightly off. With `log`, the end
of every walk is recorded as `sim.arrival`: where the robot really stopped against where it believes it stopped, for
the gate; the runtime never reads it.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Literal

import mujoco
import numpy as np

from spingi.adapters.sim_mujoco.contacts import ground_geoms, pushed_obstacle, subtree_geoms, touched_obstacle
from spingi.adapters.sim_mujoco.locomotion import (
    FollowGains,
    FollowState,
    Walker,
    base_pose,
    follow,
    make_walker,
    ramp,
)
from spingi.adapters.sim_mujoco.objects import SceneObjects
from spingi.adapters.sim_mujoco.odometry import Odometry, OdometryErrors, as_pose, compose, inverse, planar
from spingi.adapters.sim_mujoco.rendering import HeadCamera, Renderers, Videos
from spingi.adapters.sim_mujoco.scene import OFFSCREEN_SIZE, CompiledScene, write_scene
from spingi.adapters.sim_mujoco.terrain import fill_heights
from spingi.core.ports import (
    Arm,
    CapabilityMissing,
    EventSink,
    Frame,
    GripResult,
    JointState,
    RobotEstopped,
    RobotFell,
)
from spingi.core.types import Pose2D, Pose3D
from spingi.robots import RobotProfile, get_profile

Locomotion = Literal["kinematic", "physics"]
OdometryMode = Literal["truth", "drift"]
FOOT_FRICTION = 0.05  # below any ground's: MuJoCo takes the larger of the two, so the ground decides the grip

EstopEngaged = RobotEstopped  # kept as an alias for existing imports


class WatchdogExpired(RuntimeError):
    """The safety monitor's heartbeat is older than the watchdog allows."""


class SimAdapter:
    YIELD_EVERY = 10  # ticks between cooperative yields in fast mode (0.2 s of simulated time)
    ZNEAR_M, ZFAR_M = 0.03, 200.0  # what the cameras draw: from 3 cm to 200 m
    # physics locomotion
    SETTLE_S = 0.5  # standing still after the reset, before the run starts: the feet find the ground
    STOP_SETTLE_S = 0.3  # zero command after arriving, so the robot stands on both feet before reporting
    CONTACT_TICKS = 5  # an obstacle pushed for this long (0.1 s) blocks the walk
    CONTACT_FORCE_N = 40.0  # a push, not a brush: a hand grazing a table while turning does not stop the robot
    STUCK_S = 4.0  # no progress for this long: the robot is stuck (a kerb it cannot climb, a slope too steep)
    MAX_ACCEL = (1.0, 1.0, 2.5)  # command ramp, per second: vx, vy (m/s^2) and wz (rad/s^2)
    TILT_FALL = 0.5  # the base's up axis below this (60 degrees off vertical): the robot is down
    START_JITTER_M = 0.03
    START_JITTER_RAD = 0.05

    def __init__(
        self,
        scene_path: Path | str,
        robot: RobotProfile | str = "g1",
        model_dir: Path | str | None = None,
        dt: float = 0.02,
        speed_cap: float = 1.0,
        yaw_rate: float = 1.0,
        realtime: bool = False,
        viewer: bool = False,
        record_dir: Path | None = None,
        record_video: bool = False,
        record_camera: bool = False,
        record_every: int = 5,
        sample_every: int = 5,
        battery_pct: float = 100.0,
        battery_drain_per_m: float = 0.5,
        watchdog_ms: int | None = None,
        grasp_range_m: float = 0.9,
        camera_size: tuple[int, int] = OFFSCREEN_SIZE,
        locomotion: Locomotion = "kinematic",
        start: Pose2D | None = None,
        seed: int = 0,
        odometry: OdometryMode = "truth",
        camera_noise: bool = False,
        log: EventSink | None = None,
    ) -> None:
        self.profile = get_profile(robot)
        self.capabilities = self.profile.capabilities
        self.scene_path = Path(scene_path)
        self.model_dir = Path(model_dir) if model_dir is not None else self.profile.sim.path
        self.model, self.data, self.scene = self._load_model()
        self._root_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, self.profile.sim.root_body)
        self._robot_geoms = subtree_geoms(self.model, self._root_body)  # the whole robot collides, not just its base
        self._ground_geoms = ground_geoms(self.model)
        self._objects = SceneObjects(self.model, self.data, self.scene, self.profile)
        _check_modes(locomotion, odometry)
        self.locomotion: Locomotion = locomotion
        self.walker: Walker | None = self._make_walker(dt) if locomotion == "physics" else None
        if self.walker is not None and sample_every == 5:
            sample_every = 2  # 25 Hz: enough to replay the legs' motion

        self.dt = dt
        self.speed_cap = speed_cap
        self.yaw_rate = yaw_rate
        self.realtime = realtime
        self.record_dir = record_dir  # where head-camera frames (and the videos, if requested) are written
        self.record_every = record_every  # ticks between video frames: 5 at 20 ms = 10 frames per second
        self.sample_every = sample_every  # 5 ticks at 20 ms = 10 Hz
        self.battery_pct = battery_pct
        self.battery_drain_per_m = battery_drain_per_m
        self.watchdog_ms = watchdog_ms
        self.grasp_range_m = grasp_range_m  # an object within this distance of the base can be grasped
        if camera_size[0] > OFFSCREEN_SIZE[0] or camera_size[1] > OFFSCREEN_SIZE[1]:
            raise ValueError(f"camera_size {camera_size} exceeds the offscreen buffer {OFFSCREEN_SIZE}")
        self._renderers = Renderers(self.model, self.data)
        self._head_camera = HeadCamera(
            self._renderers, self.model, self.data, camera_size, camera_noise, seed, record_dir
        )
        self.sim_time_s = 0.0
        self.clock = SimClock(self)  # robot time = simulated time; the watchdog counts it too
        self._last_heartbeat = 0.0
        self._objects_dirty = False

        self.pose = start or Pose2D(x=float(self.data.qpos[0]), y=float(self.data.qpos[1]), yaw=0.0)
        told = self.pose  # where the robot is told it starts; with drift that is all it knows
        self.log = log
        self.odometry = Odometry(OdometryErrors() if odometry == "drift" else None, np.random.default_rng([seed, 1]))
        if self.walker is not None:
            self.pose = self._off_its_mark(self.pose, seed)
        self.mode: Literal["idle", "walking", "manipulating", "estop"] = "idle"
        self.estopped = False
        self.fell: str | None = None  # why and where the robot fell (physics only)
        self._command = np.zeros(3)  # velocity command applied (ramped) and wanted, body frame: vx, vy, wz
        self._wanted = np.zeros(3)
        self._ramp_step = np.array(self.MAX_ACCEL) * dt
        self.base_z = self.profile.sim.base_z
        self.roll = self.pitch = 0.0
        self.blocked_by: str | None = None
        self.stop_called = 0
        self.last_applied_speed: float | None = None
        self.ticks = 0
        self.calls: list[tuple[str, dict]] = []

        free = mujoco.mjtJoint.mjJNT_FREE
        self.joint_names = [self.model.joint(j).name for j in range(self.model.njnt) if self.model.jnt_type[j] != free]
        self._videos = Videos(self._renderers, record_dir, 1.0 / (dt * record_every), record_video, record_camera)
        self._viewer = None
        if viewer:
            self._open_viewer()
        self._apply_pose()
        if self.walker is not None:
            self._settle()
        if odometry == "drift":  # the start jitter and the settling are errors the robot does not know about
            self.odometry.relocalize(planar(self.pose), planar(told))
        self.trajectory: list[dict] = []
        self._sample(include_objects=True)

    # --- setting up ------------------------------------------------------
    def _load_model(self) -> tuple[mujoco.MjModel, mujoco.MjData, CompiledScene]:
        """The scene and the robot compiled into one MuJoCo model, and its state at the model's first keyframe."""
        xml_path, scene = write_scene(self.scene_path, self.model_dir, robot_file=self.profile.sim.file)
        try:
            model = mujoco.MjModel.from_xml_path(str(xml_path))
        finally:
            xml_path.unlink(missing_ok=True)  # compiled into the model; the file is no longer needed
        if scene.terrain is not None:
            fill_heights(model, scene.terrain, scene.terrain_spec)
        # MJCF gives the cameras' clipping planes relative to the model's size: in a 40 m yard the head camera would
        # not draw anything nearer than half a metre, such as the cabinet it inspects. In metres instead.
        model.vis.map.znear = self.ZNEAR_M / model.stat.extent
        model.vis.map.zfar = self.ZFAR_M / model.stat.extent
        data = mujoco.MjData(model)
        if model.nkey > 0:
            mujoco.mj_resetDataKeyframe(model, data, 0)
        return model, data, scene

    def _make_walker(self, dt: float) -> Walker:
        """The profile's walking controller on this model (physics locomotion), with the ground deciding the grip."""
        if self.profile.sim.policy is None:
            raise ValueError(f"{self.profile.name} has no walking policy: physics locomotion is not available")
        walker = make_walker(self.model, self.profile.sim.policy, self.profile.sim.maneuver_policy)
        if abs(dt - walker.config.control_dt) > 1e-9:
            raise ValueError(f"physics locomotion runs at the policy's period, {walker.config.control_dt} s")
        self._ground_decides_grip()
        return walker

    def _off_its_mark(self, mark: Pose2D, seed: int) -> Pose2D:
        """A real robot never starts exactly on its mark: a few centimetres and degrees, different per seed, so that
        repeated physics runs (bench) differ the way repeated real runs do."""
        rng = np.random.default_rng(seed)
        jx, jy, jyaw = rng.uniform(-1, 1, 3) * (self.START_JITTER_M, self.START_JITTER_M, self.START_JITTER_RAD)
        return Pose2D(x=mark.x + jx, y=mark.y + jy, yaw=_wrap(mark.yaw + jyaw))

    def _settle(self) -> None:
        """Standing still before the run, so the feet find the ground: not counted in the run's time."""
        for _ in range(round(self.SETTLE_S / self.dt)):
            self.walker.step(self.data, np.zeros(3))
        self._read_physics_pose()

    # --- locomotion --------------------------------------------------------
    async def walk_to(self, pose: Pose2D, max_speed: float) -> None:
        """`pose` is in the robot's own frame, like every pose the runtime handles: where it believes things are."""
        self._record("walk_to", pose=pose.model_dump(), max_speed=max_speed)
        self._guard()
        speed = min(max_speed, self.speed_cap)
        self.last_applied_speed = speed
        self.blocked_by = None
        self.mode = "walking"
        if self.walker is not None:
            await self._walk_physics(pose, speed)
        else:
            # The kinematic base never drifts: the target maps to the true frame once, through the last correction.
            await self._walk_kinematic(as_pose(compose(inverse(self.odometry.offset), planar(pose))), speed)
        self._report_arrival(pose)

    async def _walk_kinematic(self, pose: Pose2D, speed: float) -> None:
        step = speed * self.dt
        while self.mode == "walking":
            if self._watchdog_expired():
                self.blocked_by = "watchdog"
                await self.stop()
                return
            dx, dy = pose.x - self.pose.x, pose.y - self.pose.y
            dist = math.hypot(dx, dy)
            if dist <= step:
                new = Pose2D(x=pose.x, y=pose.y, yaw=self.pose.yaw)
                arrived = True
            else:
                new = Pose2D(x=self.pose.x + dx / dist * step, y=self.pose.y + dy / dist * step, yaw=math.atan2(dy, dx))
                arrived = False
            if not self._try_move(new):
                await self.stop()
                return
            self.battery_pct = max(0.0, self.battery_pct - min(dist, step) * self.battery_drain_per_m)
            await self._tick()
            if arrived:
                break
        await self._turn_to(pose.yaw)
        if self.mode == "walking":
            self.mode = "idle"
        self._sample()

    async def _walk_physics(self, pose: Pose2D, speed: float) -> None:
        """Closed loop on the robot's pose estimate, as on the real robot: velocity commands to the walking controller
        until the robot believes it is there, an obstacle holds it, it stops making progress, or it falls."""
        state, gains = FollowState(), FollowGains()
        target = (pose.x, pose.y, pose.yaw)
        best, best_t, arrived = math.inf, self.sim_time_s, False
        touching = 0
        while self.mode == "walking":
            if self._watchdog_expired():
                self.blocked_by = "watchdog"
                await self.stop()
                return
            est = self.estimate
            wanted = follow(est.x, est.y, est.yaw, target, speed, state, gains)
            if wanted is None:
                break
            self._wanted = self.walker.clip_command(*wanted)
            await self._tick()
            self._raise_if_fallen()
            hit = pushed_obstacle(self.model, self.data, self._robot_geoms, self._ground_geoms, self.CONTACT_FORCE_N)
            touching = touching + 1 if hit is not None else 0
            if touching >= self.CONTACT_TICKS:
                self.blocked_by = hit
                await self.stop()
                return
            if state.arrived != arrived:  # from walking to turning: progress is measured on the heading now
                arrived, best, best_t = state.arrived, math.inf, self.sim_time_s
            est = self.estimate
            remaining = abs(_wrap(pose.yaw - est.yaw)) if arrived else math.hypot(pose.x - est.x, pose.y - est.y)
            if remaining < best - 0.03:
                best, best_t = remaining, self.sim_time_s
            elif self.sim_time_s - best_t > self.STUCK_S:
                self.blocked_by = "stuck"
                await self.stop()
                return
        self._wanted = np.zeros(3)
        for _ in range(round(self.STOP_SETTLE_S / self.dt)):
            if self.mode != "walking":
                break
            await self._tick()
            self._raise_if_fallen()
        if self.mode == "walking":
            self.mode = "idle"
        self._sample()

    async def _turn_to(self, yaw: float) -> None:
        while self.mode == "walking":
            if self._watchdog_expired():
                self.blocked_by = "watchdog"
                await self.stop()
                return
            diff = _wrap(yaw - self.pose.yaw)
            max_step = self.yaw_rate * self.dt
            final = abs(diff) <= max_step
            target_yaw = yaw if final else self.pose.yaw + math.copysign(max_step, diff)
            if not self._try_move(Pose2D(x=self.pose.x, y=self.pose.y, yaw=target_yaw)):
                await self.stop()  # turning in place would hit something: stop instead of spinning forever
                return
            await self._tick()
            if final:
                return

    async def stop(self) -> None:
        self._record("stop")
        self.stop_called += 1
        self._wanted = np.zeros(3)  # physics: the controller brings the robot to a standstill over the next ticks
        if self.mode != "estop":
            self.mode = "idle"
        self._sample()

    async def get_pose(self) -> Pose2D:
        return self.estimate

    async def relocalize(self, pose: Pose2D) -> None:
        self._record("relocalize", pose=pose.model_dump())
        self.odometry.relocalize(planar(self.pose), planar(pose))
        self._sample()

    @property
    def estimate(self) -> Pose2D:
        """Where the robot believes it is; `pose` is where it is."""
        return as_pose(self.odometry.estimate(planar(self.pose)))

    def believed(self, x: float, y: float, yaw: float = 0.0) -> tuple[float, float, float]:
        """A true position and heading as the robot sees them, in its estimated frame: a perceiver places what the
        camera sees from where the robot believes the camera is."""
        return compose(self.odometry.offset, (x, y, yaw))

    def _report_arrival(self, target: Pose2D) -> None:
        if self.log is None:
            return
        est = self.estimate
        self.log.emit(
            "sim.arrival",
            target=_xyyaw(target),
            truth=_xyyaw(self.pose),
            estimate=_xyyaw(est),
            error_m=round(self.pose.distance_to(est), 3),
            miss_m=round(self.pose.distance_to(target), 3),
            **({"blocked_by": self.blocked_by} if self.blocked_by else {}),
        )

    # --- manipulation (v0: no real arm movement) ---------------------------
    async def move_arm(self, arm: Arm, target: Pose3D, duration_s: float) -> None:
        self._record("move_arm", arm=arm, target=target.model_dump(), duration_s=duration_s)
        self._need_arm()
        self._guard()
        for _ in range(max(1, int(duration_s / self.dt))):
            self._guard()  # e-stop or a lapsed watchdog interrupts the motion
            await self._tick()

    async def gripper(self, arm: Arm, action: Literal["open", "close"]) -> GripResult:
        self._record("gripper", arm=arm, action=action)
        self._need_arm()
        self._guard()
        released_at = None
        if action == "close" and self.held_object is None:
            if self._objects.grasp(self.pose, self.grasp_range_m):
                self._objects_dirty = True
        elif action == "open" and self.held_object is not None:
            released_at = self._objects.release(self.pose)
            self._objects_dirty = True
        self._sample(include_objects=self._objects_dirty)
        self._objects_dirty = False
        return GripResult(holding=self.held_object is not None, object_id=self.held_object, released_at=released_at)

    # --- sensors -----------------------------------------------------------
    async def get_camera(self, name: str = "head") -> Frame:
        return self._head_camera.take(name, self.sim_time_s, self.odometry.offset)

    def image(self, frame_id: str) -> np.ndarray | None:
        """The rendered image of a recent frame (RGB, height x width x 3), for a perceiver that reads pixels."""
        return self._head_camera.image(frame_id)

    @property
    def render_error(self) -> str | None:
        """Why the last render failed (no OpenGL context, for one), or None."""
        return self._renderers.error

    async def get_joint_state(self) -> JointState:
        names = [self.model.joint(i).name for i in range(1, self.model.njnt)]
        return JointState(names=names, positions=[float(v) for v in self.data.qpos[7:]])

    async def get_battery(self) -> float:
        return self.battery_pct

    # --- safety ------------------------------------------------------------
    async def estop(self) -> None:
        self._record("estop")
        self.estopped = True
        self.mode = "estop"
        self._wanted = np.zeros(3)

    async def heartbeat(self) -> None:
        self._last_heartbeat = self.sim_time_s

    def reset_estop(self) -> None:
        self.estopped = False
        self.mode = "idle"

    def set_speed_limit(self, max_speed: float) -> float:
        self.speed_cap = min(self.speed_cap, max_speed)
        return self.speed_cap

    # --- lifecycle ---------------------------------------------------------
    def close(self) -> Path | None:
        """Closes viewer and renderer and finishes the videos; returns the third-person video's path, if any."""
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None
        video = self._videos.close()
        self._renderers.close()
        return video

    # --- internals ---------------------------------------------------------
    def _try_move(self, new: Pose2D) -> bool:
        """Applies the pose; if the robot collides with an obstacle, reverts and reports it."""
        old = self.pose
        self.pose = new
        self._apply_pose()
        hit = touched_obstacle(self.model, self.data, self._robot_geoms, self._ground_geoms)
        if hit is not None:
            self.pose = old
            self._apply_pose()
            self.blocked_by = hit
            return False
        return True

    def _apply_pose(self) -> None:
        """Puts the robot at `self.pose`: on the ground's height in kinematic mode; standing, reset, in physics."""
        ground = self.scene.ground_height(self.pose.x, self.pose.y)
        if self.walker is not None:
            self.walker.reset(self.data, self.pose.x, self.pose.y, self.pose.yaw, ground)
            self._command[:] = 0.0
            self._read_physics_pose()
            return
        q = self.data.qpos
        q[0], q[1], q[2] = self.pose.x, self.pose.y, ground + self.profile.sim.base_z
        q[3], q[4], q[5], q[6] = math.cos(self.pose.yaw / 2), 0.0, 0.0, math.sin(self.pose.yaw / 2)
        self.base_z = float(q[2])
        self.data.qvel[:] = 0.0
        self._objects.follow_hand(self.pose)
        mujoco.mj_forward(self.model, self.data)

    def _ground_decides_grip(self) -> None:
        """The robot models give their feet contact priority, so the feet's friction would apply on any ground. Here
        the feet keep their softness but take a negligible friction: the surface's friction applies (terrain.py)."""
        for g in self._robot_geoms:
            if self.model.geom_priority[g] > 0:
                self.model.geom_priority[g] = 0
                self.model.geom_friction[g][0] = FOOT_FRICTION

    def _read_physics_pose(self) -> None:
        """The pose is what the simulation says, and the held object follows the hand."""
        x, y, z, roll, pitch, yaw = base_pose(self.data)
        self.pose = Pose2D(x=x, y=y, yaw=yaw)
        self.base_z, self.roll, self.pitch = z, roll, pitch
        self._objects.follow_hand(self.pose)

    def _check_fall(self) -> None:
        cfg = self.walker.config
        up = float(self.data.xmat[self._root_body].reshape(3, 3)[2, 2])
        above = self.base_z - self.scene.ground_height(self.pose.x, self.pose.y)
        if up < self.TILT_FALL or above < cfg.fall_height_m:
            self.fell = f"the robot fell at ({self.pose.x:.2f}, {self.pose.y:.2f})"
            self.estopped = True
            self.mode = "estop"
            self._wanted = np.zeros(3)

    # --- objects (kinematic grasp, ADR-0007) --------------------------------
    def _need_arm(self) -> None:
        if "arm" not in self.capabilities:
            raise CapabilityMissing(f"{self.profile.name} has no arm")

    async def _tick(self) -> None:
        if self.walker is not None:
            wanted = self._wanted if self.mode == "walking" else np.zeros(3)
            self._command = ramp(self._command, wanted, self._ramp_step)
            if hasattr(self.walker, "wanted"):
                self.walker.wanted = wanted
            before = self.pose
            self.walker.step(self.data, self._command)
            self._read_physics_pose()
            ground = self.scene.terrain
            surface = ground.surface_at(self.pose.x, self.pose.y) if ground is not None else None
            self.odometry.update(planar(before), planar(self.pose), self.dt, surface)
            self.battery_pct = max(0.0, self.battery_pct - before.distance_to(self.pose) * self.battery_drain_per_m)
            if self.fell is None:
                self._check_fall()
        self.ticks += 1
        self.sim_time_s += self.dt
        self.clock.advance()  # wake whoever sleeps on the simulated clock
        if self.ticks % self.sample_every == 0:
            self._sample(include_objects=self.held_object is not None)
        if self._viewer is not None:
            self._viewer.sync()
        if self.ticks % self.record_every == 0:
            self._videos.add(self.sim_time_s)
        if self.realtime:
            await asyncio.sleep(self.dt)
        elif self.ticks % self.YIELD_EVERY == 0:
            await asyncio.sleep(0)  # lets independent tasks (safety monitor, console) run in fast mode

    def _sample(self, include_objects: bool = False) -> None:
        robot = {
            "x": round(self.pose.x, 4),
            "y": round(self.pose.y, 4),
            "yaw": round(self.pose.yaw, 4),
            "mode": self.mode,
        }
        if self.walker is not None or self.scene.terrain is not None:  # the base leaves the flat floor
            robot.update(z=round(self.base_z, 4), roll=round(self.roll, 4), pitch=round(self.pitch, 4))
        row = {"t": round(self.sim_time_s, 3), "robot": robot, "battery_pct": round(self.battery_pct, 2)}
        if max(abs(v) for v in self.odometry.offset) > 1e-4:  # the robot believes it is elsewhere
            row["estimate"] = {k: round(v, 4) for k, v in zip(("x", "y", "yaw"), planar(self.estimate), strict=True)}
        if include_objects:
            row["objects"] = self.object_positions()
        if self.walker is not None:
            q = self.data.qpos
            row["joints"] = {
                n: round(float(q[self.model.jnt_qposadr[self.model.joint(n).id]]), 3) for n in self.joint_names
            }
        self.trajectory.append(row)

    def camera_video_for_viewer(self) -> dict | None:
        """The head-camera video as the episode's manifest describes it, or None if none was written."""
        return self._videos.head_for_manifest()

    def terrain_for_viewer(self) -> dict | None:
        """The ground as the episode's `terrain.json`, or None on a flat floor."""
        return self.scene.viewer_terrain()

    def object_positions(self) -> dict[str, dict[str, float]]:
        """Ground-truth world positions of the scene objects, keyed by object id (used by SimPerceiver)."""
        return self._objects.positions()

    @property
    def held_object(self) -> str | None:
        """The object in the robot's hand, by id (kinematic grasp, ADR-0007): it follows the hand."""
        return self._objects.held

    def _open_viewer(self) -> None:
        import mujoco.viewer

        try:
            self._viewer = mujoco.viewer.launch_passive(self.model, self.data)
        except RuntimeError as exc:
            raise RuntimeError("viewer unavailable: on macOS launch with `uv run mjpython -m spingi.cli ...`") from exc

    def _record(self, name: str, **kw) -> None:
        self.calls.append((name, kw))

    def _guard(self) -> None:
        """Every motion command checks the e-stop and the watchdog (safety layers S0/S1) before it acts."""
        self._raise_if_fallen()
        if self.estopped:
            raise EstopEngaged("e-stop engaged: no motion command accepted")
        if self._watchdog_expired():
            self.blocked_by = "watchdog"
            raise WatchdogExpired("no heartbeat from the safety monitor: motion refused")

    def _raise_if_fallen(self) -> None:
        if self.fell is not None:
            raise RobotFell(self.fell)

    def _watchdog_expired(self) -> bool:
        if self.watchdog_ms is None:
            return False
        return (self.sim_time_s - self._last_heartbeat) * 1000 > self.watchdog_ms


def _check_modes(locomotion: str, odometry: str) -> None:
    if locomotion not in ("kinematic", "physics"):
        raise ValueError(f"unknown locomotion {locomotion!r}: kinematic or physics")
    if odometry not in ("truth", "drift"):
        raise ValueError(f"unknown odometry {odometry!r}: truth or drift")
    if odometry == "drift" and locomotion != "physics":
        raise ValueError("odometry drift needs physics locomotion: the kinematic base moves exactly")


class SimClock:
    """Simulated time: `sleep` returns once the simulation has advanced by `seconds`, costing nothing while idle."""

    def __init__(self, adapter: SimAdapter) -> None:
        self._adapter = adapter
        self._advanced = asyncio.Event()

    def now(self) -> float:
        return self._adapter.sim_time_s

    def advance(self) -> None:
        """Called by the adapter at every tick."""
        self._advanced.set()
        self._advanced = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        target = self._adapter.sim_time_s + seconds
        while self._adapter.sim_time_s < target - 1e-9:
            await self._advanced.wait()


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _xyyaw(p: Pose2D) -> dict[str, float]:
    return {"x": round(p.x, 3), "y": round(p.y, 3), "yaw": round(p.yaw, 3)}
