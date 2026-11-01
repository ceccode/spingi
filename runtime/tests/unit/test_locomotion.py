"""The parts of physics walking that need no physics: going to a pose as velocity commands, the command ramp, the
policy files and the switch between a walking and a manoeuvring policy (ADR-0015)."""

import json
import math

import mujoco
import numpy as np
import pytest

from spingi.adapters.sim_mujoco.locomotion import (
    POLICIES_DIR,
    FollowState,
    GaitSwitch,
    MlpPolicy,
    PolicyConfig,
    WalkingController,
    follow,
    load_policy,
    ramp,
)
from spingi.robots import PROFILES


def test_far_behind_turns_on_the_spot_first_then_walks_and_steers():
    state = FollowState()
    vx, vy, wz = follow(0, 0, 0, (-3, 0.1, 0), 0.5, state)
    assert vx == 0 and vy == 0 and abs(wz) > 0
    vx, vy, wz = follow(0, 0, 0, (3, 0.3, 0), 0.5, state)
    assert vx == pytest.approx(0.5, abs=0.01) and vy == 0 and wz > 0  # steering towards the waypoint, not sidestepping


def test_close_to_the_waypoint_it_slows_down_and_sidesteps():
    vx, vy, wz = follow(0, 0, 0, (0.2, 0.2, 0), 0.8, FollowState())
    assert 0 < vx < 0.8 and vy > 0 and wz == 0


def test_once_there_it_turns_to_the_heading_and_then_says_done():
    state = FollowState()
    vx, vy, wz = follow(0, 0, 0, (0.02, 0, math.pi / 2), 0.5, state)
    assert state.arrived and (vx, vy) == (0, 0) and wz >= 0.3
    assert follow(0, 0, math.pi / 2, (0.02, 0, math.pi / 2), 0.5, state) is None


def test_pushed_off_the_waypoint_while_turning_it_walks_back():
    state = FollowState(arrived=True)
    vx, _, _ = follow(-0.3, 0, 0, (0, 0, 1.0), 0.5, state)
    assert not state.arrived and vx > 0


def test_the_command_ramps_instead_of_jumping():
    step = np.array([0.02, 0.02, 0.05])
    cmd = ramp(np.zeros(3), np.array([1.0, 0.0, -1.0]), step)
    assert np.allclose(cmd, [0.02, 0.0, -0.05])
    for _ in range(100):
        cmd = ramp(cmd, np.zeros(3), step)
    assert not cmd.any()  # exactly zero once there: the gait switch relies on it


@pytest.mark.parametrize("name", sorted(p.name for p in POLICIES_DIR.iterdir() if p.is_dir()))
def test_every_policy_loads_and_matches_its_robot_model(name):
    config, policy = load_policy(name)
    robot = next(p for p in PROFILES.values() if p.name == config.robot)
    assert name in (robot.sim.policy, robot.sim.maneuver_policy)
    model = mujoco.MjModel.from_xml_path(str(robot.sim.path / robot.sim.file))
    controller = WalkingController(model, config, policy)  # checks the network's size against the observation
    data = mujoco.MjData(model)
    controller.reset(data, 0.0, 0.0, 0.0, 0.0)
    history = np.tile(controller._observation(data, np.zeros(3)), config.history)
    action = policy(history, controller.frame)
    assert action.shape == (len(config.joint_names),) and np.isfinite(action).all()
    assert (POLICIES_DIR / name / "README.md").exists() and (POLICIES_DIR / name / "LICENSE").exists()


def test_an_estimator_feeds_velocity_and_a_unit_latent_to_the_actor():
    rng = np.random.default_rng(0)
    est = [(rng.normal(size=(8, 6)), np.zeros(8)), (rng.normal(size=(7, 8)), np.zeros(7))]  # 3 velocity + 4 latent
    actor = [(np.eye(3 + 3 + 4), np.zeros(10))]  # identity: shows what the actor received
    out = MlpPolicy(actor, estimator=est)(rng.normal(size=6), frame=3)
    assert np.isclose(np.linalg.norm(out[6:]), 1.0)


class FakeController:
    def __init__(self, name):
        self.config = PolicyConfig.load(POLICIES_DIR / "go2_himloco" / "policy.json")
        self.name, self.steps, self.handovers = name, 0, 0
        self.last_action = np.zeros(12)

    def step(self, data, command):
        self.steps += 1

    def handover(self):
        self.handovers += 1

    def reset(self, *a):
        pass

    def clip_command(self, *v):
        return np.array(v)


def test_the_manoeuvring_policy_turns_and_creeps_the_walking_one_walks():
    walk, slow = FakeController("walk"), FakeController("slow")
    gait = GaitSwitch(walk, slow)
    gait.wanted = np.array([0.0, 0.0, 1.0])
    gait.step(None, np.array([0.5, 0.0, 0.0]))  # still running: no handover at speed
    assert gait.active is walk
    gait.step(None, np.array([0.1, 0.0, 0.5]))
    assert gait.active is slow and slow.handovers == 1
    gait.wanted = np.array([0.6, 0.0, 0.0])
    gait.step(None, np.array([0.1, 0.0, 0.0]))
    assert gait.active is walk and gait.switches == 2
    gait.wanted = np.zeros(3)  # standing still: the walking policy stands
    gait.step(None, np.zeros(3))
    assert gait.active is walk


def test_stepping_back_or_aside_is_a_manoeuvre_even_when_fast():
    walk, slow = FakeController("walk"), FakeController("slow")
    gait = GaitSwitch(walk, slow)
    for wanted in ([-0.3, 0.2, 0.0], [0.1, 0.4, 0.0], [0.25, 0.3, 0.0]):  # back at an angle, aside, wide of ahead
        gait.wanted = np.array(wanted)
        gait.step(None, np.zeros(3))
        assert gait.active is slow, wanted
    gait.wanted = np.array([0.5, 0.2, 0.0])  # mostly forward
    gait.step(None, np.zeros(3))
    assert gait.active is walk


def test_policy_json_files_are_complete():
    for folder in (f for f in POLICIES_DIR.iterdir() if f.is_dir()):
        raw = json.loads((folder / "policy.json").read_text())
        n = len(raw["joint_names"])
        assert len(raw["default_q"]) == len(raw["action_scale"]) == len(raw["kp"]) == len(raw["kd"]) == n
        assert set(raw["command_limits"]) == {"vx", "vy", "wz"}
