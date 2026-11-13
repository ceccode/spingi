"""Which geoms are the robot and which the ground, and what the robot touches, read from MuJoCo's contacts.

Functions of the model and its state: nothing is kept here.
"""

from __future__ import annotations

import mujoco
import numpy as np


def subtree_geoms(model: mujoco.MjModel, root_body: int) -> set[int]:
    """The geoms of a body and of every body below it: the whole robot, not just its base."""
    bodies = set()
    for b in range(model.nbody):
        cur = b
        while cur > 0:
            if cur == root_body:
                bodies.add(b)
                break
            cur = model.body_parentid[cur]
    return {g for g in range(model.ngeom) if model.geom_bodyid[g] in bodies}


def ground_geoms(model: mujoco.MjModel) -> set[int]:
    """The floor and the terrain: what the robot stands on, never an obstacle."""
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or "" for g in range(model.ngeom)]
    return {g for g, n in enumerate(names) if n == "floor" or n.startswith("ter_")}


def touched_obstacle(model: mujoco.MjModel, data: mujoco.MjData, robot: set[int], ground: set[int]) -> str | None:
    """The first thing the robot is in contact with that is neither itself nor the ground, if any."""
    for i in range(data.ncon):
        other = _other(data.contact[i], robot, ground)
        if other is not None:
            return _name(model, other)
    return None


def pushed_obstacle(
    model: mujoco.MjModel, data: mujoco.MjData, robot: set[int], ground: set[int], min_force_n: float
) -> str | None:
    """The obstacle the robot pushes on hardest, when the push is at least `min_force_n` in total: a push, not a
    brush."""
    force: dict[int, float] = {}
    f = np.zeros(6)
    for i in range(data.ncon):
        other = _other(data.contact[i], robot, ground)
        if other is None:
            continue
        mujoco.mj_contactForce(model, data, i, f)
        force[other] = force.get(other, 0.0) + abs(float(f[0]))
    if not force:
        return None
    other, total = max(force.items(), key=lambda kv: kv[1])
    if total < min_force_n:
        return None
    return _name(model, other)


def _other(contact, robot: set[int], ground: set[int]) -> int | None:
    """The geom the robot touches in this contact; None for robot-robot, world-world and the ground."""
    a_robot, b_robot = contact.geom1 in robot, contact.geom2 in robot
    if a_robot == b_robot:
        return None
    other = contact.geom2 if a_robot else contact.geom1
    return None if other in ground else other


def _name(model: mujoco.MjModel, geom: int) -> str:
    return mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom) or f"geom{geom}"
