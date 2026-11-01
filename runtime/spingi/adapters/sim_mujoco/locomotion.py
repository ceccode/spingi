"""Walking in physics with learned controllers, standing in for the vendor's controller on the robot (ADR-0015).

A walking policy is a small neural network trained in simulation by others and published with its weights: every
20 ms it reads the robot's body (gyroscope, gravity direction, joint angles and speeds, its own last output) and a
velocity command, and returns joint targets that a PD loop tracks at 200 Hz. The weights live in
`runtime/sim/policies/<name>/` as plain arrays (`policy.npz`) with a `policy.json` that says how to build the
observation and drive the joints, so inference is numpy only (`scripts/import_policies.py` made them).

A robot may use two policies, one to walk and one to manoeuvre, turning on the spot and moving slowly (`GaitSwitch`):
no single published Go2 policy does both well in MuJoCo, as the vendor's controller does on the robot.

`follow` turns "go to this pose" into velocity commands: the part of `walk_to` that the runtime will run on the real
robot too, above the vendor's controller (ADR-0013).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import mujoco
import numpy as np

POLICIES_DIR = Path(__file__).resolve().parents[3] / "sim" / "policies"

ObservationTerm = Literal["ang_vel", "gravity", "command", "phase", "joint_pos", "joint_vel", "last_action"]


@dataclass(frozen=True)
class PolicyConfig:
    """`policy.json`: everything the network was trained with that the simulator has to reproduce."""

    name: str
    robot: str  # robot profile name it was trained for
    source: str  # where the weights come from, for the record
    joint_names: tuple[str, ...]  # the network's joint order
    default_q: tuple[float, ...]
    action_scale: tuple[float, ...]
    kp: tuple[float, ...]
    kd: tuple[float, ...]
    actuation: Literal["position", "torque"]  # MuJoCo position servos with these gains, or a PD loop computing torque
    observation: tuple[ObservationTerm, ...]
    obs_scale: dict[str, float]
    command_scale: tuple[float, float, float]
    command_limits: dict[str, tuple[float, float]]  # vx, vy, wz the network was trained on
    phase_period_s: float | None = None  # gait clock, for networks that take one
    history: int = 1  # observation frames the network reads, the latest first
    obs_clip: float | None = None
    joint_damping: float | None = None  # replaces the model's passive joint damping when set
    stand_height_m: float = 0.8  # base height above the ground when it is reset standing
    fall_height_m: float = 0.4  # base lower than this above the ground: the robot is down
    control_dt: float = 0.02
    physics_dt: float = 0.005

    @classmethod
    def load(cls, path: Path) -> PolicyConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["command_limits"] = {k: tuple(v) for k, v in raw["command_limits"].items()}
        for key in ("joint_names", "default_q", "action_scale", "kp", "kd", "observation", "command_scale"):
            raw[key] = tuple(raw[key])
        return cls(**raw)


Layers = list[tuple[np.ndarray, np.ndarray]]


def _mlp(layers: Layers, x: np.ndarray) -> np.ndarray:
    for i, (w, b) in enumerate(layers):
        x = w @ x + b
        if i < len(layers) - 1:
            x = np.where(x > 0, x, np.expm1(np.minimum(x, 0.0)))  # ELU
    return x


class MlpPolicy:
    """A multilayer perceptron with ELU between layers, an optional input normaliser, and an optional estimator.

    With an estimator (HIMLoco), the estimator reads the whole observation history and returns a body velocity
    (3 values) and a terrain latent (normalised to unit length); the actor reads the latest frame, the velocity and
    the latent.
    """

    def __init__(
        self,
        layers: Layers,
        mean: np.ndarray | None = None,
        std: np.ndarray | None = None,
        estimator: Layers | None = None,
    ):
        self.layers, self.estimator = layers, estimator
        self.mean, self.std = mean, std
        self.n_in = (estimator[0][0] if estimator else layers[0][0]).shape[1]
        self.n_out = layers[-1][0].shape[0]

    @classmethod
    def load(cls, path: Path) -> MlpPolicy:
        with np.load(path) as z:
            layers = _layers(z, "")
            estimator = _layers(z, "e_")
            mean = z["obs_mean"].astype(np.float64) if "obs_mean" in z.files else None
            std = z["obs_std"].astype(np.float64) if "obs_std" in z.files else None
        return cls(layers, mean, std, estimator or None)

    def __call__(self, obs: np.ndarray, frame: int | None = None) -> np.ndarray:
        """`obs` is the network's whole input (the history, latest frame first); `frame` the length of one frame."""
        x = obs if self.mean is None else (obs - self.mean) / self.std
        if self.estimator is None:
            return _mlp(self.layers, x)
        e = _mlp(self.estimator, x)
        latent = e[3:] / max(float(np.linalg.norm(e[3:])), 1e-12)
        return _mlp(self.layers, np.concatenate([x[:frame], e[:3], latent]))


def _layers(z, prefix: str) -> Layers:
    """`<prefix>w0, <prefix>b0, <prefix>w1, ...` from an npz archive, as float64."""
    out: Layers = []
    while f"{prefix}w{len(out)}" in z.files:
        i = len(out)
        out.append((z[f"{prefix}w{i}"].astype(np.float64), z[f"{prefix}b{i}"].astype(np.float64)))
    return out


def load_policy(name: str) -> tuple[PolicyConfig, MlpPolicy]:
    folder = POLICIES_DIR / name
    return PolicyConfig.load(folder / "policy.json"), MlpPolicy.load(folder / "policy.npz")


class WalkingController:
    """Runs a walking policy on a MuJoCo model: configures the actuators once, then one `step` per control period."""

    def __init__(self, model: mujoco.MjModel, config: PolicyConfig, policy: MlpPolicy) -> None:
        if model.jnt_type[0] != mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError("the robot's first joint must be its floating base")
        self.model, self.config, self.policy = model, config, policy
        ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, n) for n in config.joint_names]
        if min(ids) < 0:
            raise ValueError(f"policy {config.name}: joints missing in the model")
        self.qadr = np.array([model.jnt_qposadr[j] for j in ids])
        self.vadr = np.array([model.jnt_dofadr[j] for j in ids])
        by_joint = {int(model.actuator_trnid[a, 0]): a for a in range(model.nu)}
        self.act = np.array([by_joint[j] for j in ids])
        self.default_q = np.array(config.default_q)
        self.scale = np.array(config.action_scale)
        self.kp, self.kd = np.array(config.kp), np.array(config.kd)
        self.ctrl_lo = model.actuator_ctrlrange[self.act, 0].copy()
        self.ctrl_hi = model.actuator_ctrlrange[self.act, 1].copy()
        self.decimation = round(config.control_dt / config.physics_dt)
        model.opt.timestep = config.physics_dt
        if config.actuation == "position":
            # Position servos with the gains the network was trained with; the reflected rotor inertia as in training.
            omega = 2 * math.pi * 10.0
            for i, a in enumerate(self.act):
                model.actuator_gainprm[a, 0] = self.kp[i]
                model.actuator_biasprm[a, 1] = -self.kp[i]
                model.actuator_biasprm[a, 2] = -self.kd[i]
                model.dof_armature[self.vadr[i]] = self.kp[i] / omega**2
        if config.joint_damping is not None:
            model.dof_damping[self.vadr] = config.joint_damping
        self.last_action = np.zeros(len(ids))
        self.target = self.default_q.copy()
        self.steps = 0
        self._history: np.ndarray | None = None
        frame = self._observation(mujoco.MjData(model), np.zeros(3)).shape[0]
        if frame * config.history != policy.n_in or policy.n_out != len(ids):
            raise ValueError(
                f"policy {config.name}: network {policy.n_in}->{policy.n_out}, "
                f"config {frame}x{config.history}->{len(ids)}"
            )
        self.frame = frame

    def reset(self, data: mujoco.MjData, x: float, y: float, yaw: float, ground_z: float) -> None:
        """The robot standing still at a pose, joints at the trained default."""
        data.qpos[0:3] = (x, y, ground_z + self.config.stand_height_m)
        data.qpos[3:7] = (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))
        data.qpos[self.qadr] = self.default_q
        data.qvel[:] = 0.0
        self.handover()
        if self.config.actuation == "position":
            data.ctrl[self.act] = self.default_q
        mujoco.mj_forward(self.model, data)

    def handover(self) -> None:
        """Forget the past: the network starts from the current state (after a reset, or taking over from another)."""
        self.last_action[:] = 0.0
        self._history = None
        self.target = self.default_q.copy()
        self.steps = 0

    def step(self, data: mujoco.MjData, command: np.ndarray) -> None:
        """One control period: observe, infer, then track the joint targets for `decimation` physics steps."""
        obs = self._observation(data, command)
        if self.config.obs_clip is not None:
            obs = np.clip(obs, -self.config.obs_clip, self.config.obs_clip)
        if self.config.history > 1:
            if self._history is None:
                self._history = np.tile(obs, (self.config.history, 1))
            self._history = np.roll(self._history, 1, axis=0)
            self._history[0] = obs
            obs = self._history.ravel()
        action = self.policy(obs, self.frame)
        self.last_action = action
        self.target = self.default_q + self.scale * action
        for _ in range(self.decimation):
            if self.config.actuation == "position":
                data.ctrl[self.act] = self.target
            else:
                q, dq = data.qpos[self.qadr], data.qvel[self.vadr]
                tau = self.kp * (self.target - q) - self.kd * dq
                data.ctrl[self.act] = np.clip(tau, self.ctrl_lo, self.ctrl_hi)
            mujoco.mj_step(self.model, data)
        self.steps += 1

    def _observation(self, data: mujoco.MjData, command: np.ndarray) -> np.ndarray:
        c = self.config
        rot = _quat_to_mat(data.qpos[3:7])
        parts: list[np.ndarray] = []
        for term in c.observation:
            s = c.obs_scale.get(term, 1.0)
            if term == "ang_vel":
                parts.append(data.qvel[3:6] * s)  # the free joint's angular velocity is in the body frame
            elif term == "gravity":
                parts.append(rot.T @ np.array([0.0, 0.0, -1.0]))
            elif term == "command":
                parts.append(command * np.array(c.command_scale))
            elif term == "phase":
                t = self.steps * c.control_dt
                ph = 2 * math.pi * ((t % c.phase_period_s) / c.phase_period_s)
                walking = np.linalg.norm(command) >= 0.1
                parts.append(np.array([math.sin(ph), math.cos(ph)]) if walking else np.zeros(2))
            elif term == "joint_pos":
                parts.append((data.qpos[self.qadr] - self.default_q) * s)
            elif term == "joint_vel":
                parts.append(data.qvel[self.vadr] * s)
            elif term == "last_action":
                parts.append(self.last_action)
            else:
                raise ValueError(f"unknown observation term {term!r}")
        return np.concatenate(parts)

    def clip_command(self, vx: float, vy: float, wz: float) -> np.ndarray:
        lim = self.config.command_limits
        return np.array(
            [float(np.clip(vx, *lim["vx"])), float(np.clip(vy, *lim["vy"])), float(np.clip(wz, *lim["wz"]))]
        )

    def describe(self) -> str:
        return self.config.name


class GaitSwitch:
    """Two policies on one robot: one walks, the other manoeuvres (turns on the spot, moves slowly into position).

    The walking one drives when the robot is wanted to go faster than a manoeuvre and mostly forwards; the
    manoeuvring one otherwise, backwards and sideways included (HIMLoco stalls stepping back at an angle). They hand
    over only while the commanded speed is low on both sides of the change, so neither takes over a running robot.
    """

    SLOW = 0.2  # m/s: wanted linear speeds up to this are manoeuvres
    FORWARD = 0.7  # cosine of the widest angle off straight ahead that is still walking (about 45 degrees)

    def __init__(self, walk: WalkingController, maneuver: WalkingController) -> None:
        self.walk, self.maneuver = walk, maneuver
        self.active = walk
        self.config = walk.config  # stand and fall heights come from the walking policy
        self.switches = 0
        self.wanted = np.zeros(3)  # set by the caller before each step: where the command is heading

    def reset(self, data: mujoco.MjData, x: float, y: float, yaw: float, ground_z: float) -> None:
        self.active = self.walk
        self.walk.reset(data, x, y, yaw, ground_z)
        self.maneuver.handover()

    def step(self, data: mujoco.MjData, command: np.ndarray) -> None:
        wanted_speed = float(np.hypot(self.wanted[0], self.wanted[1]))
        still = not self.wanted.any()
        walking = wanted_speed > self.SLOW and self.wanted[0] >= self.FORWARD * wanted_speed
        choice = self.walk if (walking or still) else self.maneuver
        if choice is not self.active and float(np.hypot(command[0], command[1])) <= self.SLOW:
            choice.handover()
            self.active = choice
            self.switches += 1
        self.active.step(data, command)

    @property
    def last_action(self) -> np.ndarray:
        return self.active.last_action

    def clip_command(self, vx: float, vy: float, wz: float) -> np.ndarray:
        return self.walk.clip_command(vx, vy, wz)

    def describe(self) -> str:
        return f"{self.walk.config.name} (walking) + {self.maneuver.config.name} (turning, slow moves)"


Walker = WalkingController | GaitSwitch


def make_walker(model: mujoco.MjModel, policy: str, maneuver_policy: str | None = None) -> Walker:
    walk = WalkingController(model, *load_policy(policy))
    if maneuver_policy is None:
        return walk
    return GaitSwitch(walk, WalkingController(model, *load_policy(maneuver_policy)))


def base_pose(data: mujoco.MjData) -> tuple[float, float, float, float, float, float]:
    """x, y, z, roll, pitch, yaw of the floating base."""
    w, x, y, z = (float(v) for v in data.qpos[3:7])
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return float(data.qpos[0]), float(data.qpos[1]), float(data.qpos[2]), roll, pitch, yaw


def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, q)
    return m.reshape(3, 3)


# --- going to a pose ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FollowGains:
    arrive_m: float = 0.06  # close enough to the waypoint
    yaw_tol_rad: float = 0.08  # close enough to the final heading
    turn_first_rad: float = 0.6  # facing further off than this, turn on the spot before walking
    k_dist: float = 1.2  # forward speed per metre to go, near the end
    k_yaw: float = 1.5  # yaw rate per radian of heading error
    v_min: float = 0.15  # slowest forward command that still makes the robot step
    w_min: float = 0.3  # slowest turning command that still turns it
    lateral_within_m: float = 0.5  # sidestep only for the last half metre


DEFAULT_GAINS = FollowGains()


@dataclass
class FollowState:
    arrived: bool = False  # the position is reached; only the heading is left


def follow(
    x: float,
    y: float,
    yaw: float,
    target: tuple[float, float, float],
    max_speed: float,
    state: FollowState,
    gains: FollowGains = DEFAULT_GAINS,
) -> tuple[float, float, float] | None:
    """Body-frame velocity command (vx, vy, wz) that brings the robot to `target` (x, y, yaw); None once there.

    Walk towards the waypoint while steering, after turning on the spot if it is behind; slow down over the last
    metre, sidestep the last centimetres instead of circling; then turn to the final heading.
    """
    tx, ty, tyaw = target
    dx, dy = tx - x, ty - y
    dist = math.hypot(dx, dy)
    if state.arrived and dist > 2.0 * gains.arrive_m:
        state.arrived = False  # pushed away while turning: walk back
    if not state.arrived and dist <= gains.arrive_m:
        state.arrived = True
    if not state.arrived:
        bearing = wrap(math.atan2(dy, dx) - yaw)
        if abs(bearing) > gains.turn_first_rad and dist > gains.lateral_within_m:
            return 0.0, 0.0, _at_least(gains.k_yaw * bearing, gains.w_min)
        speed = max(gains.v_min, min(max_speed, gains.k_dist * dist))
        vx = speed * math.cos(bearing)
        vy = speed * math.sin(bearing) if dist <= gains.lateral_within_m else 0.0
        wz = gains.k_yaw * bearing if dist > gains.lateral_within_m else 0.0
        return vx, vy, wz
    err = wrap(tyaw - yaw)
    if abs(err) <= gains.yaw_tol_rad:
        return None
    return 0.0, 0.0, _at_least(gains.k_yaw * err, gains.w_min)


def _at_least(w: float, minimum: float) -> float:
    return math.copysign(max(abs(w), minimum), w)


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def ramp(current: np.ndarray, wanted: np.ndarray, max_delta: np.ndarray) -> np.ndarray:
    """Moves a command towards the wanted one by at most `max_delta` per control period: no instant accelerations."""
    out = current + np.clip(wanted - current, -max_delta, max_delta)
    out[np.abs(out) < 1e-9] = 0.0
    return out
