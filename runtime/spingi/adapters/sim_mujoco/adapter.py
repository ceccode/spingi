"""SimAdapter: Unitree G1 in MuJoCo with a kinematically moved base.

What it simulates in v0 (ADR-0006): base pose at the requested speed with clamping, collisions with
obstacles (the robot stops where it hits), battery, e-stop, watchdog, rendered head camera,
third-person video recording. What it does NOT simulate: real walking (the legs stay in the
"stand" pose), arm dynamics, falling.
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Literal

import mujoco
import numpy as np

from spingi.adapters.sim_mujoco.scene import DEFAULT_MODEL_DIR, write_scene
from spingi.core.ports import Arm, Frame, GripResult, JointState
from spingi.core.types import Pose2D, Pose3D


class EstopEngaged(RuntimeError):
    pass


class SimAdapter:
    BASE_Z = 0.79  # pelvis height in the "stand" keyframe of the menagerie model
    YIELD_EVERY = 10  # ticks between cooperative yields in fast mode (0.2 s of simulated time)

    def __init__(
        self,
        scene_path: Path | str,
        model_dir: Path | str = DEFAULT_MODEL_DIR,
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
    ) -> None:
        self.scene_path = Path(scene_path)
        self.model_dir = Path(model_dir)
        xml_path = write_scene(self.scene_path, self.model_dir)
        try:
            self.model = mujoco.MjModel.from_xml_path(str(xml_path))
        finally:
            xml_path.unlink(missing_ok=True)  # compiled into the model; the file is no longer needed
        self.data = mujoco.MjData(self.model)
        if self.model.nkey > 0:
            mujoco.mj_resetDataKeyframe(self.model, self.data, 0)

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
        self.sim_time_s = 0.0
        self.clock = SimClock(self)  # robot time = simulated time; the watchdog counts it too
        self._advanced = asyncio.Event()
        self._last_heartbeat = 0.0
        self.held_object: str | None = None  # kinematic grasp (ADR-0007): the object follows the hand
        self._objects_dirty = False

        self.pose = Pose2D(x=float(self.data.qpos[0]), y=float(self.data.qpos[1]), yaw=0.0)
        self.mode: Literal["idle", "walking", "manipulating", "estop"] = "idle"
        self.estopped = False
        self.blocked_by: str | None = None
        self.stop_called = 0
        self.last_applied_speed: float | None = None
        self.ticks = 0
        self.calls: list[tuple[str, dict]] = []

        self._robot_geoms = self._subtree_geoms("pelvis")
        self._floor = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        self._renderer: mujoco.Renderer | None = None
        self.render_error: str | None = None
        self._frames: list[np.ndarray] = []
        self._frame_counter = 0
        self._viewer = None
        if viewer:
            self._open_viewer()
        self._apply_pose()
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

    async def _turn_to(self, yaw: float) -> None:
        while self.mode == "walking":
            diff = _wrap(yaw - self.pose.yaw)
            max_step = self.yaw_rate * self.dt
            if abs(diff) <= max_step:
                self._try_move(Pose2D(x=self.pose.x, y=self.pose.y, yaw=yaw))
                await self._tick()
                return
            self._try_move(Pose2D(x=self.pose.x, y=self.pose.y, yaw=self.pose.yaw + math.copysign(max_step, diff)))
            await self._tick()

    async def stop(self) -> None:
        self._record("stop")
        self.stop_called += 1
        if self.mode != "estop":
            self.mode = "idle"
        self._sample()

    async def get_pose(self) -> Pose2D:
        return self.pose

    # --- manipulation (v0: no real arm movement) ---------------------------
    async def move_arm(self, arm: Arm, target: Pose3D, duration_s: float) -> None:
        self._record("move_arm", arm=arm, target=target.model_dump(), duration_s=duration_s)
        self._guard()
        for _ in range(max(1, int(duration_s / self.dt))):
            await self._tick()

    async def gripper(self, arm: Arm, action: Literal["open", "close"]) -> GripResult:
        self._record("gripper", arm=arm, action=action)
        self._guard()
        released_at = None
        if action == "close" and self.held_object is None:
            self.held_object = self._nearest_object(self.grasp_range_m)
            if self.held_object is not None:
                self._objects_dirty = True
                self._apply_pose()
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
        frame = Frame(id=f"sim-{self._frame_counter}", ts=self.sim_time_s, camera=name, width=320, height=240)
        img = self._render(name, 240, 320)
        if img is not None and self.record_dir is not None:
            import imageio.v3 as iio

            out = self.record_dir / "frames" / f"{frame.id}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            iio.imwrite(out, img)
            frame.data_ref = str(out)
        return frame

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
        if self._frames and self.record_dir is not None and self.record_video:
            import imageio.v2 as iio

            self.record_dir.mkdir(parents=True, exist_ok=True)
            video = self.record_dir / "run.mp4"
            fps = max(1, round(1.0 / (self.dt * self.record_every)))
            iio.mimwrite(video, self._frames, fps=fps, codec="libx264", quality=7, macro_block_size=1)
            self._frames = []
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
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
        q = self.data.qpos
        q[0], q[1], q[2] = self.pose.x, self.pose.y, self.BASE_Z
        q[3], q[4], q[5], q[6] = math.cos(self.pose.yaw / 2), 0.0, 0.0, math.sin(self.pose.yaw / 2)
        self.data.qvel[:] = 0.0
        if self.held_object is not None:
            g = self._object_geom(self.held_object)
            self.model.geom_pos[g] = (
                self.pose.x + self.HAND_FORWARD_M * math.cos(self.pose.yaw),
                self.pose.y + self.HAND_FORWARD_M * math.sin(self.pose.yaw),
                self.HAND_HEIGHT_M,
            )
        mujoco.mj_forward(self.model, self.data)

    # --- objects (kinematic grasp, ADR-0007) --------------------------------
    HAND_FORWARD_M = 0.35
    HAND_HEIGHT_M = 0.95

    def _object_geom(self, object_id: str) -> int:
        return mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f"obj_{object_id}")

    def _nearest_object(self, max_dist: float) -> str | None:
        best, best_d = None, max_dist
        for oid, pos in self.object_positions().items():
            d = math.hypot(pos["x"] - self.pose.x, pos["y"] - self.pose.y)
            if d < best_d:
                best, best_d = oid, d
        return best

    def _release(self, object_id: str) -> None:
        """Puts the object down in front of the robot, on the highest obstacle top below the hand, else on the floor."""
        g = self._object_geom(object_id)
        x = self.pose.x + self.HAND_FORWARD_M * math.cos(self.pose.yaw)
        y = self.pose.y + self.HAND_FORWARD_M * math.sin(self.pose.yaw)
        half_h = float(self.model.geom_size[g][2])
        z = self._surface_height(x, y) + half_h
        self.model.geom_pos[g] = (x, y, z)
        mujoco.mj_forward(self.model, self.data)

    def _surface_height(self, x: float, y: float) -> float:
        top = 0.0
        for g in range(self.model.ngeom):
            name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            if not name.startswith("obs_"):
                continue
            cx, cy, cz = (float(v) for v in self.data.geom_xpos[g])
            hx, hy, hz = (float(v) for v in self.model.geom_size[g])
            if abs(x - cx) <= hx and abs(y - cy) <= hy:
                top = max(top, cz + hz)
        return top

    def _collision(self) -> str | None:
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            a, b = c.geom1, c.geom2
            a_robot, b_robot = a in self._robot_geoms, b in self._robot_geoms
            if a_robot == b_robot:
                continue  # robot-robot or world-world
            other = b if a_robot else a
            if other == self._floor:
                continue
            return mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, other) or f"geom{other}"
        return None

    async def _tick(self) -> None:
        self.ticks += 1
        self.sim_time_s += self.dt
        self._advanced.set()  # wake whoever sleeps on the simulated clock
        self._advanced = asyncio.Event()
        if self.ticks % self.sample_every == 0:
            self._sample(include_objects=self.held_object is not None)
        if self._viewer is not None:
            self._viewer.sync()
        if self.record_video and self.record_dir is not None and self.ticks % self.record_every == 0:
            img = self._render("track", 480, 640)
            if img is not None:
                self._frames.append(img)
        if self.realtime:
            await asyncio.sleep(self.dt)
        elif self.ticks % self.YIELD_EVERY == 0:
            await asyncio.sleep(0)  # lets independent tasks (safety monitor, console) run in fast mode

    def _sample(self, include_objects: bool = False) -> None:
        row = {
            "t": round(self.sim_time_s, 3),
            "robot": {
                "x": round(self.pose.x, 4),
                "y": round(self.pose.y, 4),
                "yaw": round(self.pose.yaw, 4),
                "mode": self.mode,
            },
            "battery_pct": round(self.battery_pct, 2),
        }
        if include_objects:
            row["objects"] = self.object_positions()
        self.trajectory.append(row)

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
            if self._renderer is None or self._renderer.height != height or self._renderer.width != width:
                if self._renderer is not None:
                    self._renderer.close()
                self._renderer = mujoco.Renderer(self.model, height, width)
            self._renderer.update_scene(self.data, camera=camera)
            return self._renderer.render().copy()
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
        if self.estopped:
            raise EstopEngaged("e-stop engaged: no motion command accepted")

    def _watchdog_expired(self) -> bool:
        if self.watchdog_ms is None:
            return False
        return (self.sim_time_s - self._last_heartbeat) * 1000 > self.watchdog_ms


class SimClock:
    """Simulated time: `sleep` returns once the simulation has advanced by `seconds`, costing nothing while idle."""

    def __init__(self, adapter: SimAdapter) -> None:
        self._adapter = adapter

    def now(self) -> float:
        return self._adapter.sim_time_s

    async def sleep(self, seconds: float) -> None:
        target = self._adapter.sim_time_s + seconds
        while self._adapter.sim_time_s < target - 1e-9:
            await self._adapter._advanced.wait()


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
