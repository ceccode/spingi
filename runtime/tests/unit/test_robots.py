"""Robot profiles (ADR-0012): data the runtime and the simulator read, never code paths per robot."""

import pytest

from spingi.core.ports import ALL_CAPABILITIES
from spingi.robots import DEFAULT_ROBOT, G1, GO2, PROFILES, get_profile


def test_the_default_robot_is_the_g1_with_every_capability():
    assert get_profile(DEFAULT_ROBOT) is G1
    assert G1.capabilities == ALL_CAPABILITIES and G1.kind == "humanoid"


def test_the_go2_is_a_quadruped_without_an_arm():
    assert GO2.kind == "quadruped"
    assert GO2.capabilities == {"locomotion", "camera"} and not GO2.has("arm")
    assert GO2.sim.hand_forward_m is None and GO2.sim.hand_height_m is None


def test_profiles_are_found_by_alias_and_by_episode_name():
    for profile in PROFILES.values():
        assert get_profile(profile.alias) is profile
        assert get_profile(profile.name) is profile
        assert get_profile(profile) is profile
    with pytest.raises(KeyError, match="unknown robot 'spot'"):
        get_profile("spot")


def test_every_profile_points_at_a_vendored_model():
    for profile in PROFILES.values():
        assert (profile.sim.path / profile.sim.file).is_file(), profile.name
        assert (profile.sim.path / "LICENSE").is_file(), f"{profile.name}: a vendored model carries its license"


def test_profiles_are_immutable_data():
    with pytest.raises(Exception, match="frozen"):
        GO2.name = "something-else"  # type: ignore[misc]
