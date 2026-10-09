"""Robot profiles: what a robot is and what it can do (ADR-0012).

A profile is data. The runtime reads `capabilities` to decide which skills a plan may use; the simulator reads
`sim` to load the right model and move it. The adapters stay the only code that talks to a robot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from spingi.core.ports import ALL_CAPABILITIES, Capability

RobotKind = Literal["humanoid", "quadruped"]

MODELS_DIR = Path(__file__).resolve().parent.parent / "sim" / "models"  # resolved from the package, never from cwd


class SimModel(BaseModel):
    """How `SimAdapter` drives this robot's MJCF: the model file, the body it moves and where its hand is."""

    model_config = ConfigDict(frozen=True)

    dir: str  # folder under sim/models
    file: str  # MJCF included by the generated scene
    root_body: str  # the body that carries the free joint; its subtree is "the robot" for collisions
    base_z: float  # height of the root body in the standing keyframe
    hand_forward_m: float | None = None  # where a held object sits, relative to the base (None: no arm)
    hand_height_m: float | None = None

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.dir


class RobotProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str  # the value written in episodes: manifest.robot.model
    alias: str  # the short name on the command line
    label: str  # for people and for the planner's prompt
    kind: RobotKind
    capabilities: frozenset[Capability]
    sim: SimModel

    def has(self, capability: Capability) -> bool:
        return capability in self.capabilities


G1 = RobotProfile(
    name="unitree_g1",
    alias="g1",
    label="Unitree G1, a humanoid with two arms and a head camera",
    kind="humanoid",
    capabilities=ALL_CAPABILITIES,
    sim=SimModel(
        dir="unitree_g1",
        file="g1.xml",
        root_body="pelvis",
        base_z=0.79,  # pelvis height in the "stand" keyframe of the menagerie model
        hand_forward_m=0.35,
        hand_height_m=0.95,
    ),
)

GO2 = RobotProfile(
    name="unitree_go2",
    alias="go2",
    label="Unitree Go2, a quadruped with a front camera and no arm: it cannot pick up or put down anything",
    kind="quadruped",
    capabilities=frozenset({"locomotion", "camera"}),
    sim=SimModel(
        dir="unitree_go2",
        file="go2.xml",
        root_body="base",
        base_z=0.27,  # base height in the "home" keyframe of the menagerie model
    ),
)

PROFILES: dict[str, RobotProfile] = {G1.alias: G1, GO2.alias: GO2}
DEFAULT_ROBOT = G1.alias


def get_profile(name: str | RobotProfile) -> RobotProfile:
    """By alias (`g1`) or by full name (`unitree_g1`, as written in an episode manifest)."""
    if isinstance(name, RobotProfile):
        return name
    for profile in PROFILES.values():
        if name in (profile.alias, profile.name):
            return profile
    raise KeyError(f"unknown robot '{name}'; known: {', '.join(sorted(PROFILES))}")
