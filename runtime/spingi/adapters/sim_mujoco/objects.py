"""The scene's objects and the one the robot holds: a kinematic grasp (ADR-0007), no arm dynamics.

A held object follows the hand, a point in front of the base given by the robot's profile; released, it is put down
on what is below the hand.
"""

from __future__ import annotations

import math

import mujoco

from spingi.adapters.sim_mujoco.scene import CompiledScene
from spingi.core.ports import CapabilityMissing
from spingi.core.types import Pose2D, Pose3D
from spingi.robots import RobotProfile


class SceneObjects:
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, scene: CompiledScene, profile: RobotProfile) -> None:
        self._model = model
        self._data = data
        self._scene = scene
        self._profile = profile
        self.held: str | None = None  # the object in the robot's hand, by id

    def positions(self) -> dict[str, dict[str, float]]:
        """Ground-truth world positions of the scene objects, keyed by object id."""
        out = {}
        for g in range(self._model.ngeom):
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            if name.startswith("obj_"):
                x, y, z = (float(v) for v in self._data.geom_xpos[g])
                out[name[4:]] = {"x": round(x, 4), "y": round(y, 4), "z": round(z, 4)}
        return out

    def grasp(self, robot: Pose2D, range_m: float) -> bool:
        """Takes the nearest object within `range_m` of the base into the hand; False when none is that near."""
        self.held = self._nearest(robot, range_m)
        if self.held is None:
            return False
        self.follow_hand(robot)  # into the hand; the robot itself stays where it is
        mujoco.mj_forward(self._model, self._data)
        return True

    def release(self, robot: Pose2D) -> Pose3D:
        """Puts the held object down in front of the robot, on the highest obstacle top below the hand, else on the
        ground; returns where it ended up."""
        released = self.held
        x, y = self._hand_xy(robot)
        half_h = float(self._model.geom_size[self._geom(released)][2])
        z = self._surface_height(x, y) + half_h
        self._model.body_pos[self._body(released)] = (x, y, z)
        mujoco.mj_forward(self._model, self._data)
        self.held = None
        p = self.positions()[released]
        return Pose3D(x=p["x"], y=p["y"], z=p["z"])

    def follow_hand(self, robot: Pose2D) -> None:
        """Keeps the held object in the hand as the robot moves; nothing to do with an empty hand."""
        if self.held is not None:
            x, y = self._hand_xy(robot)
            z = self._scene.ground_height(robot.x, robot.y) + self._profile.sim.hand_height_m
            self._model.body_pos[self._body(self.held)] = (x, y, z)

    def _hand_xy(self, robot: Pose2D) -> tuple[float, float]:
        """Where the hand is, in the world: in front of the base, by the profile's distance."""
        forward = self._profile.sim.hand_forward_m
        if forward is None:
            raise CapabilityMissing(f"{self._profile.name} has no arm")
        return robot.x + forward * math.cos(robot.yaw), robot.y + forward * math.sin(robot.yaw)

    def _nearest(self, robot: Pose2D, max_dist: float) -> str | None:
        best, best_d = None, max_dist
        for oid, pos in self.positions().items():
            d = math.hypot(pos["x"] - robot.x, pos["y"] - robot.y)
            if d < best_d:
                best, best_d = oid, d
        return best

    def _surface_height(self, x: float, y: float) -> float:
        top = self._scene.ground_height(x, y)
        for g in range(self._model.ngeom):
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            if not name.startswith("obs_"):
                continue
            cx, cy, cz = (float(v) for v in self._data.geom_xpos[g])
            hx, hy, hz = (float(v) for v in self._model.geom_size[g])
            if abs(x - cx) <= hx and abs(y - cy) <= hy:
                top = max(top, cz + hz)
        return top

    def _geom(self, object_id: str) -> int:
        return mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, f"obj_{object_id}")

    def _body(self, object_id: str) -> int:
        """The static body that carries the object's box and its tag decals; moving it moves them together."""
        return mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, f"objb_{object_id}")
