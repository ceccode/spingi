"""SimAdapter: a robot in MuJoCo (the Unitree G1 by default, or the Go2), moved in one of two ways.

- `kinematic` (ADR-0006, the default): the base is placed along the path at the requested speed, following the
  ground's height; the legs stay in the standing pose; touching an obstacle stops it where it is. Fast and exactly
  repeatable: bench runs and CI.
- `physics` (ADR-0015): the robot walks. A learned walking controller moves the legs, the feet push on the ground
  (asphalt, gravel, grass and their grip), the pose is whatever the physics gives, an obstacle is a real contact and
  the robot can fall. `walk_to` turns the target pose into velocity commands, as it will on the real robot.

Both: battery, e-stop, watchdog, rendered head camera, third-person video. Not simulated: arm dynamics (an object
is carried kinematically, ADR-0007). Which model is loaded and how it is moved comes from a `RobotProfile` (ADR-0012).
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Literal

import mujoco
import numpy as np

from spingi.adapters.sim_mujoco.locomotion import (
    FollowGains,
    FollowState,
    Walker,
    base_pose,
    follow,
    make_walker,
    ramp,
)
from spingi.adapters.sim_mujoco.scene import OFFSCREEN_SIZE, write_scene
from spingi.adapters.sim_mujoco.terrain import fill_heights
from spingi.core.ports import (
    Arm,
    CameraModel,
    CapabilityMissing,
    Frame,
    GripResult,
    JointState,
    RobotEstopped,
    RobotFell,
)
from spingi.core.types import Pose2D, Pose3D
from spingi.robots import RobotProfile, get_profile

Locomotion = Literal["kinematic", "physics"]
FOOT_FRICTION = 0.05  # below any ground's: MuJoCo takes the larger of the two, so the ground decides the grip

EstopEngaged = RobotEstopped  # kept as an alias for existing imports


class WatchdogExpired(RuntimeError):
    """The safety monitor's heartbeat is older than the watchdog allows."""


class SimAdapter:
    YIELD_EVERY = 10  # ticks between cooperative yields in fast mode (0.2 s of simulated time)
    IMAGE_CACHE = 4  # rendered head-camera images kept in memory for the perceiver, by frame id
    VIDEO_SIZE = (640, 480)
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
    ) -> None:
        self.profile = get_profile(robot)
        self.capabilities = self.profile.capabilities
        self.scene_path = Path(scene_path)
        self.model_dir = Path(model_dir) if model_dir is not None else self.profile.sim.path
        xml_path, self.scene = write_scene(self.scene_path, self.model_dir, robot_file=self.profile.sim.file)
        try:
            self.model = mujoco.MjModel.from_xml_path(str(xml_path))
        finally:
            xml_path.unlink(missing_ok=True)  # compiled into the model; the file is no longer needed
        if self.scene.terrain is not None:
            fill_heights(self.model, self.scene.terrain, self.scene.terrain_spec)
        self.data = mujoco.MjData(self.model)
        if self.model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        if locomotion not in ("kinematic", "physics"):
            raise ValueError(f"unknown locomotion {locomotion!r}: kinematic or physics")
        self.locomotion: Locomotion = locomotion
        self.walker: Walker | None = None
        if locomotion == "physics":
            if self.profile.sim.policy is None:
                raise ValueError(f"{self.profile.name} has no walking policy: physics locomotion is not available")
            self.walker = make_walker(self.model, self.profile.sim.policy, self.profile.sim.maneuver_policy)
            if abs(dt - self.walker.config.control_dt) > 1e-9:
                raise ValueError(f"physics locomotion runs at the policy's period, {self.walker.config.control_dt} s")
            self._ground_decides_grip()
            if sample_every == 5:
                sample_every = 2  # 25 Hz: enough to replay the legs' motion

        self.dt = dt
        self.speed_cap = speed_cap
        self.yaw_rate = yaw_rate
        self.realtime = realtime
        self.record_dir = record_dir  # where head-camera frames (and the video, if requested) are written
        self.record_video = record_video
        self.record_every = record_every
        self.sample_every = sample_every  # 5 ticks at 20 ms = 10 Hz
        self.battery_pct = battery_pct
        self.battery_drain_per_m = battery_drain_per_m
        self.watchdog_ms = watchdog_ms
        self.grasp_range_m = grasp_range_m  # an object within this distance of the base can be grasped
        if camera_size[0] > OFFSCREEN_SIZE[0] or camera_size[1] > OFFSCREEN_SIZE[1]:
            raise ValueError(f"camera_size {camera_size} exceeds the offscreen buffer {OFFSCREEN_SIZE}")
        self.camera_size = camera_size  # (width, height) of the head-camera frames
        self._images: dict[str, np.ndarray] = {}
        self.sim_time_s = 0.0
        self.clock = SimClock(self)  # robot time = simulated time; the watchdog counts it too
        self._last_heartbeat = 0.0
        self.held_object: str | None = None  # kinematic grasp (ADR-0007): the object follows the hand
        self._objects_dirty = False

        self.pose = start or Pose2D(x=float(self.data.qpos[0]), y=float(self.data.qpos[1]), yaw=0.0)
        if self.walker is not None:
            # A real robot never starts exactly on its mark: a few centimetres and degrees, different per seed, so
            # that repeated physics runs (bench) differ the way repeated real runs do.
            rng = np.random.default_rng(seed)
            jx, jy, jyaw = rng.uniform(-1, 1, 3) * (self.START_JITTER_M, self.START_JITTER_M, self.START_JITTER_RAD)
            self.pose = Pose2D(x=self.pose.x + jx, y=self.pose.y + jy, yaw=_wrap(self.pose.yaw + jyaw))
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

        self._robot_geoms = self._subtree_geoms(self.profile.sim.root_body)
        geom_names = [mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or "" for g in range(self.model.ngeom)]
        self._ground_geoms = {g for g, n in enumerate(geom_names) if n == "floor" or n.startswith("ter_")}
        free = mujoco.mjtJoint.mjJNT_FREE
        self.joint_names = [self.model.joint(j).name for j in range(self.model.njnt) if self.model.jnt_type[j] != free]
        self._renderers: dict[tuple[int, int], mujoco.Renderer] = {}  # one per (height, width): frames and video
        self.render_error: str | None = None
        self._video_writer = None  # frames are streamed to disk, never accumulated in memory
        self._frame_counter = 0
        self._viewer = None
        if viewer:
            self._open_viewer()
        self._apply_pose()
        if self.walker is not None:
            for _ in range(round(self.SETTLE_S / dt)):  # before the run: not counted in its time
                self.walker.step(self.data, np.zeros(3))
            self._read_physics_pose()
        self.trajectory: list[dict] = []
        self._sample(include_objects=True)

    # --- locomotion --------------------------------------------------------
    async def walk_to(self, pose: Pose2D, max_speed: float) -> None:
        self._record("walk_to", pose=pose.model_dump(), max_speed=max_speed)
        self._guard()
        speed = min(max_speed, self.speed_cap)
        self.last_applied_speed = speed
        self.blocked_by = None
        self.mode = "walking"
        if self.walker is not None:
            await self._walk_physics(pose, speed)
            return
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
        """Closed loop on the simulated pose: velocity commands to the walking controller until the robot is there,
        an obstacle holds it, it stops making progress, or it falls."""
        state, gains = FollowState(), FollowGains()
        target = (pose.x, pose.y, pose.yaw)
        best, best_t, arrived = math.inf, self.sim_time_s, False
        touching = 0
        while self.mode == "walking":
            if self._watchdog_expired():
                self.blocked_by = "watchdog"
                await self.stop()
                return
            wanted = follow(self.pose.x, self.pose.y, self.pose.yaw, target, speed, state, gains)
            if wanted is None:
                break
            self._wanted = self.walker.clip_command(*wanted)
            await self._tick()
            self._raise_if_fallen()
            hit = self._pushing_obstacle()
            touching = touching + 1 if hit is not None else 0
            if touching >= self.CONTACT_TICKS:
                self.blocked_by = hit
                await self.stop()
                return
            if state.arrived != arrived:  # from walking to turning: progress is measured on the heading now
                arrived, best, best_t = state.arrived, math.inf, self.sim_time_s
            remaining = (
                abs(_wrap(pose.yaw - self.pose.yaw))
                if arrived
                else math.hypot(pose.x - self.pose.x, pose.y - self.pose.y)
            )
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
        return self.pose

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
            self.held_object = self._nearest_object(self.grasp_range_m)
            if self.held_object is not None:
                self._objects_dirty = True
                self._move_held_object()  # into the hand; the robot itself stays where it is
                mujoco.mj_forward(self.model, self.data)
        elif action == "open" and self.held_object is not None:
            released = self.held_object
            self._release(released)
            self.held_object = None
            self._objects_dirty = True
            p = self.object_positions()[released]
            released_at = Pose3D(x=p["x"], y=p["y"], z=p["z"])
        self._sample(include_objects=self._objects_dirty)
        self._objects_dirty = False
        return GripResult(holding=self.held_object is not None, object_id=self.held_object, released_at=released_at)

    # --- sensors -----------------------------------------------------------
    async def get_camera(self, name: str = "head") -> Frame:
        self._frame_counter += 1
        width, height = self.camera_size
        frame = Frame(
            id=f"sim-{self._frame_counter}",
            ts=self.sim_time_s,
            camera=name,
            width=width,
            height=height,
            camera_model=self._camera_model(name, width, height),
        )
        img = self._render(name, height, width)
        if img is not None:
            self._images[frame.id] = img
            while len(self._images) > self.IMAGE_CACHE:
                del self._images[next(iter(self._images))]
            if self.record_dir is not None:
                import imageio.v3 as iio

                out = self.record_dir / "frames" / f"{frame.id}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                iio.imwrite(out, img)
                frame.data_ref = f"frames/{frame.id}.png"  # relative to the episode folder: no local paths in episodes
        return frame

    def image(self, frame_id: str) -> np.ndarray | None:
        """The rendered image of a recent frame (RGB, height x width x 3), for a perceiver that reads pixels."""
        return self._images.get(frame_id)

    def _camera_model(self, name: str, width: int, height: int) -> CameraModel | None:
        cam = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if cam < 0:
            return None
        fovy = math.radians(float(self.model.cam_fovy[cam]))
        f = (height / 2) / math.tan(fovy / 2)  # MuJoCo cameras have square pixels and a vertical field of view
        # MuJoCo camera axes: x right, y up, looking along -z. OpenCV: x right, y down, looking along +z.
        rot = self.data.cam_xmat[cam].reshape(3, 3) @ np.diag([1.0, -1.0, -1.0])
        return CameraModel(
            fx=f,
            fy=f,
            cx=width / 2,
            cy=height / 2,
            width=width,
            height=height,
            position=tuple(float(v) for v in self.data.cam_xpos[cam]),
            rotation=tuple(float(v) for v in rot.ravel()),
        )

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
        """Closes viewer and renderer; if it was recording, writes the video and returns its path."""
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None
        video = None
        if self._video_writer is not None:
            self._video_writer.close()
            self._video_writer = None
            video = self.record_dir / "run.mp4" if self.record_dir is not None else None
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
        return video

    # --- internals ---------------------------------------------------------
    def _try_move(self, new: Pose2D) -> bool:
        """Applies the pose; if the robot collides with an obstacle, reverts and reports it."""
        old = self.pose
        self.pose = new
        self._apply_pose()
        hit = self._collision()
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
        self._move_held_object()
        mujoco.mj_forward(self.model, self.data)

    def _ground_decides_grip(self) -> None:
        """The robot models give their feet contact priority, so the feet's friction would apply on any ground. Here
        the feet keep their softness but take a negligible friction: the surface's friction applies (terrain.py)."""
        for g in self._subtree_geoms(self.profile.sim.root_body):
            if self.model.geom_priority[g] > 0:
                self.model.geom_priority[g] = 0
                self.model.geom_friction[g][0] = FOOT_FRICTION

    def _read_physics_pose(self) -> None:
        """The pose is what the simulation says, and the held object follows the hand."""
        x, y, z, roll, pitch, yaw = base_pose(self.data)
        self.pose = Pose2D(x=x, y=y, yaw=yaw)
        self.base_z, self.roll, self.pitch = z, roll, pitch
        self._move_held_object()

    def _move_held_object(self) -> None:
        if self.held_object is not None:
            x, y = self._hand_xy()
            z = self.scene.ground_height(self.pose.x, self.pose.y) + self.profile.sim.hand_height_m
            self.model.body_pos[self._object_body(self.held_object)] = (x, y, z)

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

    def _hand_xy(self) -> tuple[float, float]:
        """Where the hand is, in the world: in front of the base, by the profile's distance."""
        forward = self.profile.sim.hand_forward_m
        if forward is None:
            raise CapabilityMissing(f"{self.profile.name} has no arm")
        return self.pose.x + forward * math.cos(self.pose.yaw), self.pose.y + forward * math.sin(self.pose.yaw)

    def _object_geom(self, object_id: str) -> int:
        return mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f"obj_{object_id}")

    def _object_body(self, object_id: str) -> int:
        """The static body that carries the object's box and its tag decals; moving it moves them together."""
        return mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, f"objb_{object_id}")

    def _nearest_object(self, max_dist: float) -> str | None:
        best, best_d = None, max_dist
        for oid, pos in self.object_positions().items():
            d = math.hypot(pos["x"] - self.pose.x, pos["y"] - self.pose.y)
            if d < best_d:
                best, best_d = oid, d
        return best

    def _release(self, object_id: str) -> None:
        """Puts the object down in front of the robot, on the highest obstacle top below the hand, else on the floor."""
        x, y = self._hand_xy()
        half_h = float(self.model.geom_size[self._object_geom(object_id)][2])
        z = self._surface_height(x, y) + half_h
        self.model.body_pos[self._object_body(object_id)] = (x, y, z)
        mujoco.mj_forward(self.model, self.data)

    def _surface_height(self, x: float, y: float) -> float:
        top = self.scene.ground_height(x, y)
        for g in range(self.model.ngeom):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            if not name.startswith("obs_"):
                continue
            cx, cy, cz = (float(v) for v in self.data.geom_xpos[g])
            hx, hy, hz = (float(v) for v in self.model.geom_size[g])
            if abs(x - cx) <= hx and abs(y - cy) <= hy:
                top = max(top, cz + hz)
        return top

    def _pushing_obstacle(self) -> str | None:
        """Physics: the obstacle the robot pushes on with more than CONTACT_FORCE_N in total, if any."""
        force: dict[int, float] = {}
        f = np.zeros(6)
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            a_robot, b_robot = c.geom1 in self._robot_geoms, c.geom2 in self._robot_geoms
            if a_robot == b_robot:
                continue
            other = c.geom2 if a_robot else c.geom1
            if other in self._ground_geoms:
                continue
            mujoco.mj_contactForce(self.model, self.data, i, f)
            force[other] = force.get(other, 0.0) + abs(float(f[0]))
        if not force:
            return None
        other, total = max(force.items(), key=lambda kv: kv[1])
        if total < self.CONTACT_FORCE_N:
            return None
        return mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, other) or f"geom{other}"

    def _collision(self) -> str | None:
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            a, b = c.geom1, c.geom2
            a_robot, b_robot = a in self._robot_geoms, b in self._robot_geoms
            if a_robot == b_robot:
                continue  # robot-robot or world-world
            other = b if a_robot else a
            if other in self._ground_geoms:
                continue
            return mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, other) or f"geom{other}"
        return None

    async def _tick(self) -> None:
        if self.walker is not None:
            wanted = self._wanted if self.mode == "walking" else np.zeros(3)
            self._command = ramp(self._command, wanted, self._ramp_step)
            if hasattr(self.walker, "wanted"):
                self.walker.wanted = wanted
            before = self.pose
            self.walker.step(self.data, self._command)
            self._read_physics_pose()
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
        if self.record_video and self.record_dir is not None and self.ticks % self.record_every == 0:
            img = self._render("track", self.VIDEO_SIZE[1], self.VIDEO_SIZE[0])
            if img is not None:
                self._write_video_frame(img)
        if self.realtime:
            await asyncio.sleep(self.dt)
        elif self.ticks % self.YIELD_EVERY == 0:
            await asyncio.sleep(0)  # lets independent tasks (safety monitor, console) run in fast mode

    def _write_video_frame(self, img: np.ndarray) -> None:
        if self._video_writer is None:
            import imageio.v2 as iio

            self.record_dir.mkdir(parents=True, exist_ok=True)
            fps = max(1, round(1.0 / (self.dt * self.record_every)))
            self._video_writer = iio.get_writer(
                self.record_dir / "run.mp4", fps=fps, codec="libx264", quality=7, macro_block_size=1
            )
        self._video_writer.append_data(img)

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
        if include_objects:
            row["objects"] = self.object_positions()
        if self.walker is not None:
            q = self.data.qpos
            row["joints"] = {
                n: round(float(q[self.model.jnt_qposadr[self.model.joint(n).id]]), 3) for n in self.joint_names
            }
        self.trajectory.append(row)

    def terrain_for_viewer(self) -> dict | None:
        """The ground as the episode's `terrain.json`, or None on a flat floor."""
        return self.scene.viewer_terrain()

    def object_positions(self) -> dict[str, dict[str, float]]:
        """Ground-truth world positions of the scene objects, keyed by object id (used by SimPerceiver)."""
        out = {}
        for g in range(self.model.ngeom):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            if name.startswith("obj_"):
                x, y, z = (float(v) for v in self.data.geom_xpos[g])
                out[name[4:]] = {"x": round(x, 4), "y": round(y, 4), "z": round(z, 4)}
        return out

    def _render(self, camera: str, height: int, width: int) -> np.ndarray | None:
        try:
            renderer = self._renderers.get((height, width))
            if renderer is None:
                renderer = self._renderers[(height, width)] = mujoco.Renderer(self.model, height, width)
            renderer.update_scene(self.data, camera=camera)
            return renderer.render().copy()
        except Exception as exc:  # noqa: BLE001 - no OpenGL context (headless CI): perception gets no image
            self.render_error = repr(exc)  # kept for diagnosis instead of being swallowed
            return None

    def _open_viewer(self) -> None:
        import mujoco.viewer

        try:
            self._viewer = mujoco.viewer.launch_passive(self.model, self.data)
        except RuntimeError as exc:
            raise RuntimeError("viewer unavailable: on macOS launch with `uv run mjpython -m spingi.cli ...`") from exc

    def _subtree_geoms(self, root_body: str) -> set[int]:
        root = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, root_body)
        self._root_body = root
        bodies = set()
        for b in range(self.model.nbody):
            cur = b
            while cur > 0:
                if cur == root:
                    bodies.add(b)
                    break
                cur = self.model.body_parentid[cur]
        return {g for g in range(self.model.ngeom) if self.model.geom_bodyid[g] in bodies}

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
