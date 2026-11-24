"""Skills v0. One skill per file. `default_registry()` is the whitelist used by the CLI and the tests."""

import sys

from spingi.core.skill import Skill, SkillRegistry
from spingi.skills.detect import DetectSkill
from spingi.skills.inspect import InspectSkill
from spingi.skills.navigate import NavigateSkill
from spingi.skills.pick import PickSkill
from spingi.skills.place import PlaceSkill
from spingi.skills.say import SaySkill
from spingi.skills.wait_for_human import WaitForHumanSkill


def summary(skill: Skill) -> str:
    """What a skill says about itself: the first line of its module's docstring, `name(params): what it does`."""
    doc = (sys.modules[type(skill).__module__].__doc__ or "").strip()
    return doc.splitlines()[0] if doc else skill.name


def default_registry() -> SkillRegistry:
    return SkillRegistry(
        [NavigateSkill(), DetectSkill(), PickSkill(), PlaceSkill(), InspectSkill(), WaitForHumanSkill(), SaySkill()]
    )
