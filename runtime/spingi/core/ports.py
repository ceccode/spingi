"""Ports (interfaces) the core requires and the adapters implement.

The core depends on these abstractions, never on the implementations (ADR-0001).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from spingi.core.types import ObjectRef, Pose2D, Pose3D

Arm = Literal["left", "right"]


class Frame(BaseModel):
    """An image or a reference to one. In v0 an id and the timestamp are enough."""

    id: str
    ts: float
    camera: str = "head"
    width: int = 0
    height: int = 0
    data_ref: str | None = None  # path or blob id; never the bytes in the model


class GripResult(BaseModel):
    """What the gripper reports after closing or opening. Skills check this instead of trusting the command."""

    holding: bool
    object_id: str | None = Field(default=None, description="which object is held, when the adapter can tell")
    released_at: Pose3D | None = Field(default=None, description="where a released object ended up, if known")


class JointState(BaseModel):
    names: list[str]
    positions: list[float]
    velocities: list[float] = []
    temperatures_c: list[float] = []


@runtime_checkable
class Clock(Protocol):
    """The robot's time. Wall-clock time on a real robot, simulated time in a simulator run as fast as possible.

    Anything that paces itself on the robot (the safety monitor, the watchdog, the plan deadline) uses this clock,
    so that a fast simulation is checked as often, in robot time, as the real robot would be.
    """

    def now(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


@runtime_checkable
class RobotAdapter(Protocol):
    """Translates abstract commands into calls to the robot (real or simulated). No task logic."""

    async def walk_to(self, pose: Pose2D, max_speed: float) -> None: ...
    async def stop(self) -> None: ...
    async def get_pose(self) -> Pose2D: ...
    async def move_arm(self, arm: Arm, target: Pose3D, duration_s: float) -> None: ...
    async def gripper(self, arm: Arm, action: Literal["open", "close"]) -> GripResult: ...
    async def get_camera(self, name: str = "head") -> Frame: ...
    async def get_joint_state(self) -> JointState: ...
    async def get_battery(self) -> float: ...
    async def estop(self) -> None: ...
    async def heartbeat(self) -> None: ...
    def set_speed_limit(self, max_speed: float) -> float: ...  # never raises the limit; returns the one in force

    clock: Clock  # the robot's time (see Clock)


@runtime_checkable
class Perceiver(Protocol):
    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]: ...
    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None: ...


OperatorAction = Literal["retry", "skip", "abort", "continue"]


class HumanRequest(BaseModel):
    run_id: str
    step_index: int
    skill: str
    reason: str
    options: list[OperatorAction] = ["retry", "skip", "abort"]  # a confirmation request offers continue / abort


class HumanResponse(BaseModel):
    action: OperatorAction
    note: str = ""


@runtime_checkable
class HumanGateway(Protocol):
    """Channel to the operator. The answer is one of `request.options`: no free-form input."""

    async def ask(self, request: HumanRequest, timeout_s: float) -> HumanResponse: ...


class EventSink(Protocol):
    def emit(self, kind: str, **data: Any) -> None: ...
