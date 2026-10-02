"""Skills v0. One skill per file. `default_registry()` is the whitelist used by the CLI and the tests."""

from spingi.core.skill import SkillRegistry
from spingi.skills.inspect import InspectSkill
from spingi.skills.navigate import NavigateSkill
from spingi.skills.say import SaySkill


def default_registry() -> SkillRegistry:
    return SkillRegistry([NavigateSkill(), InspectSkill(), SaySkill()])
