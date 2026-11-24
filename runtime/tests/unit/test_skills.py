import pytest
from pydantic import BaseModel

from spingi.core.skill import Skill, SkillParams, SkillRegistry
from spingi.core.types import Pose2D, RobotState
from spingi.skills import default_registry, summary
from spingi.skills.navigate import NavigateParams, NavigateSkill
from tests.conftest import world


def test_a_skill_whose_parameters_ignore_unknown_keys_cannot_be_registered():
    class LaxParams(BaseModel):  # pydantic's default: extra keys dropped without a word
        to: str

    class Lax(Skill):
        name, Params = "lax", LaxParams

    with pytest.raises(ValueError, match="lax: Params must refuse unknown keys"):
        SkillRegistry([Lax()])

    class StrictParams(SkillParams):
        to: str

    class Strict(Skill):
        name, Params = "strict", StrictParams

    assert SkillRegistry([Strict()]).names() == ["strict"]


def test_navigate_precondition_requires_known_location():
    check = NavigateSkill().preconditions(NavigateParams(to="nowhere"), world())
    assert not check.ok and "unknown location" in check.reason


def test_navigate_precondition_requires_battery():
    state = world(robot=RobotState(pose=Pose2D(x=0, y=0), battery_pct=5))
    assert not NavigateSkill().preconditions(NavigateParams(to="shelf_A"), state).ok


def test_navigate_precondition_blocks_in_estop():
    state = world(robot=RobotState(pose=Pose2D(x=0, y=0), mode="estop"))
    assert not NavigateSkill().preconditions(NavigateParams(to="shelf_A"), state).ok


def test_navigate_postcondition_checks_tolerance():
    skill, params = NavigateSkill(), NavigateParams(to="shelf_A")
    near = world(robot=RobotState(pose=Pose2D(x=0.05, y=2.05)))
    far = world(robot=RobotState(pose=Pose2D(x=1.0, y=2.0)))
    assert skill.postconditions(params, near).ok
    assert not skill.postconditions(params, far).ok


def test_every_skill_says_what_it_does_in_one_line():
    """`name(params): what it does`, the first line of the skill's module: what a planner and the API are given."""
    registry = default_registry()
    for name in registry.names():
        line = summary(registry.get(name))
        assert line.startswith(f"{name}(") and "): " in line and "\n" not in line, name
